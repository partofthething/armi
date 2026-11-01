# Copyright 2026 Ocean Atomics, LLC
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
"""Entry point for the browser-based input editor."""

from armi.cli import entryPoint


class GuiEntryPoint(entryPoint.EntryPoint):
    """Edit ARMI inputs in a browser.

    Serves the editor on loopback and opens it. Hand it a case settings file to get that case's
    blueprints alongside its settings, or a blueprints file to work on those on their own.
    """

    name = "gui"
    description = "Edit blueprints and settings in a browser"

    def addOptions(self):
        self.parser.add_argument(
            "input",
            nargs="?",
            type=str,
            default=None,
            help="A case settings file or a blueprints file to open",
        )
        self.parser.add_argument(
            "--port",
            type=int,
            default=0,
            help="Port to serve on. Defaults to any free port.",
        )
        self.parser.add_argument(
            "--host",
            type=str,
            default="127.0.0.1",
            help="Interface to serve on. Use 0.0.0.0 to reach the editor from another machine.",
        )
        self.parser.add_argument(
            "--no-browser",
            action="store_true",
            help="Print the address instead of opening a browser. Useful over SSH.",
        )

    def invoke(self):
        from armi.webui import server

        server.serve(
            path=self.args.input,
            port=self.args.port,
            host=self.args.host,
            openBrowser=not self.args.no_browser,
        )
