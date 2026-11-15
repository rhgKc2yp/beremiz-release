#!/usr/bin/env python3
"""Optional desktop smoke test of all nine BACnet object editors (needs a display)."""
from pathlib import Path
from types import SimpleNamespace
import gettext
import shutil
import sys
import tempfile

bundle = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(bundle / "beremiz"))
gettext.install("beremiz")

import wx
from CLIController import CLIController
from bacnet.BacnetSlaveEditor import ObjectEditor

app = wx.App(False)
with tempfile.TemporaryDirectory(prefix="bacnet-gui-") as directory:
    project = Path(directory) / "project"
    shutil.copytree(bundle / "beremiz/tests/projects/BACnet", project)
    controller = CLIController(SimpleNamespace(project_home=str(project), buildpath=None))
    assert not controller.check_and_load_project()
    node = controller.GetChildByName("bacnet_0")
    assert node is not None
    frame = wx.Frame(None)
    notebook = wx.Notebook(frame)
    try:
        for kind, table in node.ObjTables.items():
            editor = ObjectEditor(notebook, None, node, table)
            notebook.AddPage(editor, kind)
            editor.RefreshView()
            assert editor.VariablesGrid.GetNumberRows() == table.GetNumberRows()
        assert notebook.GetPageCount() == 9
        assert node.SaveToFile(node.GetFileName()) is True
        print("All nine BACnet object editors initialized with real wxGTK; project saved")
    finally:
        frame.Destroy()
        app.ProcessPendingEvents()
