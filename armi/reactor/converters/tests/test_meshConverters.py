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

"""Unit tests of RZ Mesh Converter."""

import math
import unittest

from armi.reactor.converters import geometryConverters, meshConverters
from armi.testing import TESTING_ROOT, loadTestReactor


class TestRZReactorMeshConverter(unittest.TestCase):
    """Loads a hex reactor and converts its mesh to RZTheta coordinates."""

    def setUp(self):
        self.o, self.r = loadTestReactor(
            inputFilePath=TESTING_ROOT, inputFileName="reactors/thirdSmallHexReactor/thirdSmallHexReactor.yaml"
        )
        self._converterSettings = {
            "uniformThetaMesh": True,
            "thetaBins": 1,
            "thetaMesh": [2 * math.pi],
            "axialMesh": [25.0, 50.0, 174.0],
            "axialSegsPerBin": 1,
        }

    def test_meshByRingCompAxialBinsSmallCore(self):
        expectedRadialMesh = [2, 3, 4, 4]
        expectedAxialMesh = [15.0, 35.32, 226.46]
        expectedThetaMesh = [2 * math.pi]

        meshConvert = meshConverters.RZThetaReactorMeshConverterByRingCompositionAxialBins(self._converterSettings)
        meshConvert.generateMesh(self.r)

        self.assertListEqual(meshConvert.radialMesh, expectedRadialMesh)
        self.assertListEqual(meshConvert.axialMesh, expectedAxialMesh)
        self.assertListEqual(meshConvert.thetaMesh, expectedThetaMesh)

    def test_meshByRingCompoAxialCoordsSmallCore(self):
        expectedRadialMesh = [2, 3, 4, 4]
        expectedAxialMesh = [25.0, 50.0, 226.46]
        expectedThetaMesh = [2 * math.pi]

        meshConvert = meshConverters.RZThetaReactorMeshConverterByRingCompositionAxialCoordinates(
            self._converterSettings
        )
        meshConvert.generateMesh(self.r)

        self.assertListEqual(meshConvert.radialMesh, expectedRadialMesh)
        self.assertListEqual(meshConvert.axialMesh, expectedAxialMesh)
        self.assertListEqual(meshConvert.thetaMesh, expectedThetaMesh)

    def test_meshByRingCompAxialFlagsSmallCore(self):
        expectedRadialMesh = [2, 3, 4, 4]
        expectedAxialMesh = [15.0, 35.32, 226.46]
        expectedThetaMesh = [2 * math.pi]

        meshConvert = meshConverters.RZThetaReactorMeshConverterByRingCompositionAxialFlags(self._converterSettings)
        meshConvert.generateMesh(self.r)

        self.assertListEqual(meshConvert.radialMesh, expectedRadialMesh)
        self.assertListEqual(meshConvert.axialMesh, expectedAxialMesh)
        self.assertListEqual(meshConvert.thetaMesh, expectedThetaMesh)

    def _growReactor(self):
        modifier = geometryConverters.FuelAssemNumModifier(self.o.cs)
        modifier.numFuelAssems = 1
        modifier.ringsToAdd = 3 * ["inner fuel"] + ["middle core fuel"]
        modifier.convert(self.r)
        self._converterSettingsLargerCore = {
            "uniformThetaMesh": True,
            "thetaBins": 1,
            "thetaMesh": [2 * math.pi],
            "axialMesh": [25.0, 30.0, 60.0, 90.0, 105.2151, 152.0, 174.0],
            "axialSegsPerBin": 2,
        }

    def test_meshByRingCompAxialBinsLargeCore(self):
        self._growReactor()
        expectedRadialMesh = [2, 3, 4, 5, 6]
        expectedAxialMesh = [35.32, 226.46]
        expectedThetaMesh = [2 * math.pi]

        meshConvert = meshConverters.RZThetaReactorMeshConverterByRingCompositionAxialBins(
            self._converterSettingsLargerCore
        )
        meshConvert.generateMesh(self.r)

        self.assertListEqual(meshConvert.radialMesh, expectedRadialMesh)
        self.assertListEqual(meshConvert.axialMesh, expectedAxialMesh)
        self.assertListEqual(meshConvert.thetaMesh, expectedThetaMesh)

    def test_meshByRingCompAxialCoordsLargeCore(self):
        self._growReactor()
        expectedRadialMesh = [2, 3, 4, 5, 6]
        expectedAxialMesh = [25.0, 30.0, 60.0, 90.0, 105.2151, 152.0, 226.46]
        expectedThetaMesh = [2 * math.pi]

        meshConvert = meshConverters.RZThetaReactorMeshConverterByRingCompositionAxialCoordinates(
            self._converterSettingsLargerCore
        )
        meshConvert.generateMesh(self.r)

        self.assertListEqual(meshConvert.radialMesh, expectedRadialMesh)
        self.assertListEqual(meshConvert.axialMesh, expectedAxialMesh)
        self.assertListEqual(meshConvert.thetaMesh, expectedThetaMesh)

    def test_meshByRingCompAxialFlagsLargeCore(self):
        self._growReactor()
        expectedRadialMesh = [2, 3, 4, 5, 6]
        expectedAxialMesh = [15.0, 35.32, 226.46]
        expectedThetaMesh = [2 * math.pi]

        meshConvert = meshConverters.RZThetaReactorMeshConverterByRingCompositionAxialFlags(
            self._converterSettingsLargerCore
        )
        meshConvert.generateMesh(self.r)

        self.assertListEqual(meshConvert.radialMesh, expectedRadialMesh)
        self.assertListEqual(meshConvert.axialMesh, expectedAxialMesh)
        self.assertListEqual(meshConvert.thetaMesh, expectedThetaMesh)


class TestMeshConverterHelpers(unittest.TestCase):
    def test_combineLastTwoRadialBins(self):
        meshConvert = meshConverters.RZThetaReactorMeshConverter({})
        meshConvert.radialMesh = [3, 5, 8, 9, 10]
        meshConvert._combineLastTwoRadialBins()
        self.assertListEqual(meshConvert.radialMesh, [3, 5, 8, 10])

        # bins that are not a single ring wide are left alone
        meshConvert.radialMesh = [3, 5, 8, 10]
        meshConvert._combineLastTwoRadialBins()
        self.assertListEqual(meshConvert.radialMesh, [3, 5, 8, 10])

    def test_checkLastValueInList(self):
        # eps is an absolute tolerance, so 399.7 is not close enough to 400
        self.assertListEqual(
            meshConverters.checkLastValueInList([100, 399.7], "test", 400, adjustLastValue=True), [100, 400]
        )
        with self.assertRaises(ValueError):
            meshConverters.checkLastValueInList([100, 399.7], "test", 400)

        # within the absolute tolerance is left unchanged
        self.assertListEqual(meshConverters.checkLastValueInList([100, 399.9995], "test", 400), [100, 399.9995])
