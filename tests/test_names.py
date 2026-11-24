"""Every global name used in the package is defined (catches lost definitions)."""
import builtins
import importlib
import importlib.util
import os
import symtable
import sys
import unittest

SRC = os.path.join(os.path.dirname(__file__), "..", "src")
sys.path.insert(0, SRC)
import _isolated  # noqa: E402,F401  (before the package: temporary folders)
PKG = os.path.join(SRC, "plasma_reopen_windows")


class TestNames(unittest.TestCase):
    def test_all_names_defined(self):
        missing = []
        for f in sorted(os.listdir(PKG)):
            if not f.endswith(".py"):
                continue
            if f == "ui.py" and importlib.util.find_spec("PySide6") is None:
                continue                     # the settings app is optional
            mod = importlib.import_module("plasma_reopen_windows." + f[:-3])

            def walk(t):
                for s in t.get_symbols():
                    name = s.get_name()
                    if not s.is_referenced() or not (s.is_global() or t.get_type() == "module"):
                        continue
                    if name == "s":          # comprehension variable (PEP 709 inlining)
                        continue
                    if not hasattr(mod, name) and not hasattr(builtins, name):
                        missing.append("%s: %s (in %s)" % (f, name, t.get_name()))
                for c in t.get_children():
                    walk(c)
            with open(os.path.join(PKG, f)) as fh:
                walk(symtable.symtable(fh.read(), f, "exec"))
        self.assertEqual(missing, [])


if __name__ == "__main__":
    unittest.main()
