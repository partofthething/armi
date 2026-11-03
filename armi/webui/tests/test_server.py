"""Tests for the editing session and the loopback server in front of it."""

import http.client
import json
import os
import unittest

from armi import configure, isConfigured

if not isConfigured():
    configure()

from armi.webui.api import Session, _looksLikeSettings
from armi.webui.document import BlueprintsDocument
from armi.webui.server import TOKEN_HEADER, GuiServer
from armi.webui.tests.helpers import inTempDir
from armi.webui.tests.test_document import SAMPLE


class TestSession(unittest.TestCase):
    def setUp(self):
        self.session = Session()
        self.session.doc = BlueprintsDocument.fromText(SAMPLE)

    def test_stateDescribesWhatIsOpen(self):
        state = self.session.state()
        self.assertTrue(state["blueprints"]["open"])
        self.assertFalse(state["settings"]["open"])

    def test_getBlueprintsCarriesGeometry(self):
        status, payload = self.session.getBlueprints()
        self.assertEqual(status, 200)
        self.assertIn("core", payload["geometry"])
        self.assertIn("iStep", payload["geometry"]["core"])

    def test_putRejectsSomethingArmiCannotRead(self):
        _status, payload = self.session.getBlueprints()
        document = payload["document"]
        document["assemblies"]["driver"]["height"] = "tall"
        status, result = self.session.putBlueprints({"document": document})
        self.assertEqual(status, 400)
        self.assertIn("error", result)

    def test_putWantsADocument(self):
        status, _payload = self.session.putBlueprints({"nope": 1})
        self.assertEqual(status, 400)

    def test_previewWorksWithoutABlockBeingValid(self):
        status, payload = self.session.previewBlock({"block": {"thing": {"shape": "Circle", "od": "a.b"}}})
        self.assertEqual(status, 200)
        self.assertEqual(payload["components"][0]["unresolved"], ["od"])

    def test_expandRejectsAGridThatIsNotThere(self):
        status, _payload = self.session.expandGrid({"name": "imaginary"})
        self.assertEqual(status, 400)

    def test_settingsComeWithGroupsAndDescriptions(self):
        status, payload = self.session.getSettings()
        self.assertEqual(status, 200)
        self.assertTrue(payload["settings"])
        one = payload["settings"][0]
        for key in ("name", "description", "group", "kind", "value", "default", "isDefault"):
            self.assertIn(key, one)

    def test_settingsAreSerializable(self):
        """The whole payload has to survive json.dumps or the editor sees nothing."""
        _status, payload = self.session.getSettings()
        json.dumps(payload)

    def test_settingsRejectNonsense(self):
        self.session.getSettings()
        status, payload = self.session.putSettings({"values": {"nSuchSettingExists": 1}})
        self.assertEqual(status, 400)
        self.assertIn("no such setting", payload["error"])

    def test_settingsAcceptAChange(self):
        self.session.getSettings()
        status, payload = self.session.putSettings({"values": {"nCycles": 7}})
        self.assertEqual(status, 200)
        value = next(each for each in payload["settings"] if each["name"] == "nCycles")
        self.assertEqual(value["value"], 7)
        self.assertFalse(value["isDefault"])

    def test_filePickerListsYaml(self):
        with inTempDir():
            with open("a.yaml", "w") as stream:
                stream.write("x: 1\n")
            os.mkdir("sub")
            status, payload = self.session.listFiles({"dir": os.getcwd()})
            self.assertEqual(status, 200)
            self.assertEqual([each["name"] for each in payload["files"]], ["a.yaml"])
            self.assertIn("sub", [each["name"] for each in payload["directories"]])

    def test_filePickerCanListAnyFileOrOnlyFolders(self):
        with inTempDir():
            for name in ("a.yaml", "lib.bin"):
                with open(name, "w") as stream:
                    stream.write("x")
            os.mkdir("sub")
            _, anything = self.session.listFiles({"dir": os.getcwd(), "kind": "any"})
            self.assertEqual(sorted(each["name"] for each in anything["files"]), ["a.yaml", "lib.bin"])
            _, folders = self.session.listFiles({"dir": os.getcwd(), "kind": "dir"})
            self.assertEqual(folders["files"], [])
            self.assertIn("sub", [each["name"] for each in folders["directories"]])
            status, _ = self.session.listFiles({"dir": os.getcwd(), "kind": "socket"})
            self.assertEqual(status, 400)


class TestFileKindDetection(unittest.TestCase):
    def test_tellsSettingsFromBlueprints(self):
        with inTempDir():
            with open("cs.yaml", "w") as stream:
                stream.write("# a comment first\n\nsettings:\n  nCycles: 1\n")
            with open("bp.yaml", "w") as stream:
                stream.write("blocks:\n  fuel: {}\n")
            self.assertTrue(_looksLikeSettings("cs.yaml"))
            self.assertFalse(_looksLikeSettings("bp.yaml"))

    def test_missingFileIsNotSettings(self):
        self.assertFalse(_looksLikeSettings("not-here.yaml"))


class TestServer(unittest.TestCase):
    """Exercises the HTTP layer, including the things that stop it being driven from elsewhere."""

    @classmethod
    def setUpClass(cls):
        session = Session()
        session.doc = BlueprintsDocument.fromText(SAMPLE)
        cls.server = GuiServer(session).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.stop()

    def request(self, method, path, body=None, token=True, host=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.port, timeout=10)
        headers = {"Content-Type": "application/json"}
        if token:
            headers[TOKEN_HEADER] = self.server.token
        if host:
            headers["Host"] = host
        connection.request(method, path, json.dumps(body) if body is not None else None, headers)
        response = connection.getresponse()
        raw = response.read()
        connection.close()
        return response.status, raw

    def json(self, *args, **kwargs):
        status, raw = self.request(*args, **kwargs)
        return status, json.loads(raw)

    def test_servesThePage(self):
        status, raw = self.request("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn(b"<title>ARMI</title>", raw)

    def test_servesModulesAsJavascript(self):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.port, timeout=10)
        connection.request("GET", "/js/main.js")
        response = connection.getresponse()
        response.read()
        connection.close()
        # A module served as text/plain will not load in any browser.
        self.assertIn("text/javascript", response.getheader("Content-Type"))

    def test_refusesToLeaveTheStaticDirectory(self):
        status, _raw = self.request("GET", "/../../document.py")
        self.assertIn(status, (403, 404))

    def test_apiNeedsTheToken(self):
        status, _raw = self.request("GET", "/api/state", token=False)
        self.assertEqual(status, 403)

    def test_apiRejectsAWrongToken(self):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.port, timeout=10)
        connection.request("GET", "/api/state", headers={TOKEN_HEADER: "not-the-token"})
        response = connection.getresponse()
        response.read()
        connection.close()
        self.assertEqual(response.status, 403)

    def test_tokenMayComeFromTheUrl(self):
        status, _payload = self.json("GET", f"/api/state?t={self.server.token}", token=False)
        self.assertEqual(status, 200)

    def test_refusesANonLoopbackHost(self):
        """Closes the door on DNS rebinding, which is how a web page reaches a local port."""
        status, _raw = self.request("GET", "/api/state", host="reactor.example.com")
        self.assertEqual(status, 421)

    def test_answersAnyHostWhenBoundWide(self):
        """Other machines reach it by its own name, so only the token keeps them out."""
        wide = GuiServer(self.server.session, host="0.0.0.0").start()
        try:
            connection = http.client.HTTPConnection("127.0.0.1", wide.port, timeout=10)
            for token, expected in ((wide.token, 200), ("not-the-token", 403)):
                connection.request("GET", "/api/state", headers={"Host": "reactor.example.com", TOKEN_HEADER: token})
                response = connection.getresponse()
                response.read()
                self.assertEqual(response.status, expected)
            connection.close()
        finally:
            wide.stop()

    def test_state(self):
        status, payload = self.json("GET", "/api/state")
        self.assertEqual(status, 200)
        self.assertTrue(payload["blueprints"]["open"])

    def test_catalog(self):
        status, payload = self.json("GET", "/api/catalog")
        self.assertEqual(status, 200)
        for key in ("materials", "shapes", "flags", "geometries"):
            self.assertTrue(payload[key], key)

    def test_blueprintsRoundTripOverHttp(self):
        status, payload = self.json("GET", "/api/blueprints")
        self.assertEqual(status, 200)
        document = payload["document"]
        document["grids"]["core"]["cells"]["0,0"] = "BL"

        status, payload = self.json("PUT", "/api/blueprints", {"document": document})
        self.assertEqual(status, 200)
        self.assertEqual(payload["document"]["grids"]["core"]["cells"]["0,0"], "BL")

    def test_yaml(self):
        status, payload = self.json("GET", "/api/blueprints/yaml")
        self.assertEqual(status, 200)
        self.assertIn("blocks:", payload["yaml"])

    def test_unknownRoute(self):
        status, _payload = self.json("GET", "/api/nothing-here")
        self.assertEqual(status, 404)

    def test_malformedBody(self):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.port, timeout=10)
        connection.request(
            "PUT", "/api/blueprints", "{not json", {TOKEN_HEADER: self.server.token, "Content-Type": "application/json"}
        )
        response = connection.getresponse()
        response.read()
        connection.close()
        self.assertEqual(response.status, 400)

    def test_saveWritesWhereTold(self):
        with inTempDir():
            target = os.path.join(os.getcwd(), "written.yaml")
            status, payload = self.json("POST", "/api/blueprints/save", {"path": target})
            self.assertEqual(status, 200)
            self.assertTrue(os.path.exists(payload["path"]))


class TestOpening(unittest.TestCase):
    """Opening a case settings file should bring that case's blueprints with it."""

    def _writeCase(self, loadingFile="bp.yaml", extraSetting=""):
        with open("bp.yaml", "w") as stream:
            stream.write(SAMPLE)
        with open("case.yaml", "w") as stream:
            stream.write("settings:\n  nCycles: 3\n")
            stream.write(f"  loadingFile: {loadingFile}\n")
            stream.write(extraSetting)

    def test_settingsFileBringsItsBlueprints(self):
        with inTempDir():
            self._writeCase()
            session = Session()
            status, payload = session.open({"path": "case.yaml"})
            self.assertEqual(status, 200)
            self.assertEqual(payload["opened"], "case")
            self.assertTrue(session.state()["blueprints"]["open"])
            self.assertTrue(session.state()["settings"]["open"])
            self.assertIn("core", payload["document"]["grids"])

    def test_blueprintsFileOpensOnItsOwn(self):
        with inTempDir():
            self._writeCase()
            session = Session()
            status, payload = session.open({"path": "bp.yaml"})
            self.assertEqual(status, 200)
            self.assertEqual(payload["opened"], "blueprints")
            self.assertFalse(session.state()["settings"]["open"])

    def test_missingBlueprintsIsReportedNotCrashed(self):
        with inTempDir():
            self._writeCase(loadingFile="not-there.yaml")
            session = Session()
            status, payload = session.open({"path": "case.yaml"})
            self.assertEqual(status, 409)
            self.assertIn("not-there.yaml", payload["error"])
            # The settings still opened, so the editor has something to show.
            self.assertTrue(session.state()["settings"]["open"])

    def test_missingFile(self):
        session = Session()
        status, _payload = session.open({"path": "definitely-not-here.yaml"})
        self.assertEqual(status, 400)

    def test_settingsThisArmiCannotPlaceAreReportedNotPrompted(self):
        """Loading must not stop at the terminal to ask; the person is looking at a browser."""
        with inTempDir():
            self._writeCase(extraSetting="  noSuchSettingAnywhere: 4\n")
            session = Session()
            status, _payload = session.open({"path": "case.yaml"})
            self.assertEqual(status, 200)
            self.assertIn("noSuchSettingAnywhere", session.settings.ignored)

            _status, described = session.getSettings()
            self.assertIn("noSuchSettingAnywhere", described["ignored"])

    def test_savingWouldNotDropSettingsSilently(self):
        with inTempDir():
            self._writeCase(extraSetting="  noSuchSettingAnywhere: 4\n")
            session = Session()
            session.open({"path": "case.yaml"})

            status, payload = session.saveSettings({})
            self.assertEqual(status, 409)
            self.assertIn("noSuchSettingAnywhere", payload["error"])

            # Somewhere else is fine.
            status, _payload = session.saveSettings({"path": "other.yaml"})
            self.assertEqual(status, 200)
