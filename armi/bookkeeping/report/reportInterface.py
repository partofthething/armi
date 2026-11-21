# Copyright 2019 TerraPower, LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Interface for ARMI reporting needs of ARMI, including PDF format."""

import re

from armi import interfaces, runLog
from armi.bookkeeping import report
from armi.bookkeeping.report import reportingUtils
from armi.physics import neutronics
from armi.physics.neutronics.settings import CONF_NEUTRONICS_TYPE
from armi.reactor.flags import Flags
from armi.utils import reportPlotting, units

ORDER = interfaces.STACK_ORDER.BEFORE + interfaces.STACK_ORDER.BOOKKEEPING


def describeInterfaces(cs):
    """Function for exposing interface(s) to other code."""
    if cs["genReports"]:
        return (ReportInterface, {})
    return None


class ReportInterface(interfaces.Interface):
    """An interface to manage the use of the report system."""

    name = "report"

    reports = set()

    def __init__(self, r, cs):
        interfaces.Interface.__init__(self, r, cs)
        self.fuelCycleSummary = {"bocFissile": 0.0}

    def distributable(self):
        """Disables distributing of this report by broadcast MPI."""
        return self.Distribute.SKIP

    def interactBOL(self):
        interfaces.Interface.interactBOL(self)
        runLog.important("Beginning of BOL Reports")
        reportingUtils.makeCoreAndAssemblyMaps(self.r, self.cs)
        reportingUtils.writeAssemblyMassSummary(self.r)

        if self.cs["summarizeAssemDesign"]:
            reportingUtils.summarizePinDesign(self.r.core)

        runLog.info(report.ALL[report.RUN_META])

    def interactEveryNode(self, cycle, node):
        self.r.core.calcBlockMaxes()

        runLog.important(f"Cycle {cycle}, node {node} Summary: ")
        runLog.important(
            f"  time= {self.r.p.time:8.2f} years, keff= {self.r.core.p.keff:.12f} "
            f"maxPD= {self.r.core.p.maxPD:-8.2f} MW/m^2, maxBuI= {self.r.core.p.maxBuI:-8.4f} "
            f"maxBuF= {self.r.core.p.maxBuF:8.4f}"
        )

        if self.cs["plots"]:
            adjoint = self.cs[CONF_NEUTRONICS_TYPE] == neutronics.ADJREAL_CALC
            figName = self.cs.caseTitle + f"_{cycle}_{node}" + ".mgFlux." + self.cs["outputFileExtension"]

            if self.r.core.getFirstBlock(Flags.FUEL).p.mgFlux is not None:
                from armi.reactor import blocks

                blocks.Block.plotFlux(self.r.core, fName=figName, peak=True, adjoint=adjoint)
            else:
                runLog.info("No mgFlux to plot in reports")

    def interactBOC(self, cycle=None):
        self.fuelCycleSummary["bocFissile"] = self.r.core.getTotalBlockParam("kgFis")

    def interactEOC(self, cycle=None):
        reportingUtils.writeCycleSummary(self.r.core)
        runLog.info(self.o.timer.report(inclusionCutoff=0.001))

    def generateDesignReport(self, generateFullCoreMap, showBlockAxMesh):
        reportingUtils.makeCoreDesignReport(self.r.core, self.cs)
        reportingUtils.makeCoreAndAssemblyMaps(self.r, self.cs, generateFullCoreMap, showBlockAxMesh)
        reportingUtils.makeBlockDesignReport(self.r)

    def interactEOL(self):
        """Adds the data to the report, and generates it."""
        b = self.r.core.getFirstBlock(Flags.FUEL)
        b.setAreaFractionsReport()

        dbi = self.o.getInterface("database")
        buGroups = self.cs["buGroups"]
        history = self.o.getInterface("history")
        reportPlotting.plotReactorPerformance(
            self.r,
            dbi,
            buGroups,
            extension=self.cs["outputFileExtension"],
            history=history,
        )

        reportingUtils.setNeutronBalancesReport(self.r.core)
        self.writeRunSummary()
        self.o.timer.stopAll()  # consider the run done
        runLog.info(self.o.timer.report(inclusionCutoff=0.001, totalTime=True))
        runLog.info(self.printReports())

    def printReports(self):
        """Report Interface Specific."""
        str_ = ""
        for report_ in self.reports:
            str_ += re.sub("\n", "\n\t", f"{report_}")

        return "---------- REPORTS BEGIN ----------\n" + str_ + "\n----------- REPORTS END -----------"

    def writeRunSummary(self):
        """Make a summary of the run."""
        # spent fuel pool report
        if self.r.excore.get("sfp") is not None:
            self.reportSFP(self.r.excore["sfp"])
            self.countAssembliesSFP(self.r.excore["sfp"])

    @staticmethod
    def reportSFP(sfp):
        """A high-level summary of the Spent Fuel Pool."""
        # Do not print this report if the SFP is the ARMI default, or empty, as it will not be interesting to the user.
        if sfp.numColumns is None or not len(sfp):
            return

        title = "SpentFuelPool Report"
        runLog.important("-" * len(title))
        runLog.important(title)
        runLog.important("-" * len(title))
        totFis = 0.0
        for a in sfp:
            runLog.important(
                "{assembly:15s} discharged at t={dTime:10f} after {residence:10f} yrs. It entered at cycle: {cycle}. "
                "It has {fiss:10f} kg (x {mult}) fissile and peak BU={bu:.2f} %.".format(
                    assembly=a,
                    dTime=a.p.dischargeTime,
                    residence=(a.p.dischargeTime - a.p.chargeTime),
                    cycle=a.p.chargeCycle,
                    fiss=a.getFissileMass(),
                    bu=a.getMaxParam("percentBu"),
                    mult=a.p.multiplicity,
                )
            )
            totFis += a.getFissileMass() * a.p.multiplicity / 1000  # convert to kg

        runLog.important(f"Total SFP fissile inventory of {sfp} is {totFis / 1000.0:.4E} MT")

    @staticmethod
    def countAssembliesSFP(sfp):
        """Report on the count of assemblies in the SFP at each timestep."""
        if not len(sfp):
            return

        runLog.important("Count:")
        thisTimeCount = 0
        a = sfp[0]
        lastTime = a.getAge() / units.DAYS_PER_YEAR + a.p.chargeTime

        for totCount, a in enumerate(sfp):
            thisTime = a.getAge() / units.DAYS_PER_YEAR + a.p.chargeTime

            if thisTime != lastTime:
                runLog.important(
                    f"Number of assemblies moved at t={lastTime:6.2f}: {thisTimeCount:04d}. Cumulative: {totCount:04d}"
                )
                lastTime = thisTime
                thisTimeCount = 0
            thisTimeCount += 1
