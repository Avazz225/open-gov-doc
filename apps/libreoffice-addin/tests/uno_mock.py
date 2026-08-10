"""Minimaler Fake der UNO-Laufzeitumgebung, damit `settings_store.py`/
`ogdoc_addin.py` mit reinem `python3 -m unittest` importiert und teilweise
ausgeführt werden können - es gibt in dieser Entwicklungsumgebung keinen
funktionierenden UNO-Skript-Bridge-Zugang (siehe ADR 0046 "Konsequenzen"),
das hier ist das Äquivalent zu apps/office-addin/tests/office-mock.ts.

Installiert Fake-Module unter `com.sun.star.*` sowie `unohelper` in
`sys.modules`, BEVOR die eigentlichen Add-in-Module importiert werden.
"""

from __future__ import annotations

import sys
import types


def install():
    beans = types.ModuleType("com.sun.star.beans")

    class PropertyAttribute:
        REMOVABLE = 1 << 8

    class PropertyValue:
        def __init__(self):
            self.Name = ""
            self.Value = None

    beans.PropertyAttribute = PropertyAttribute
    beans.PropertyValue = PropertyValue

    awt = types.ModuleType("com.sun.star.awt")

    class XActionListener:
        pass

    awt.XActionListener = XActionListener

    star = types.ModuleType("com.sun.star")
    star.beans = beans
    star.awt = awt
    sun = types.ModuleType("com.sun")
    sun.star = star
    com = types.ModuleType("com")
    com.sun = sun

    sys.modules["com"] = com
    sys.modules["com.sun"] = sun
    sys.modules["com.sun.star"] = star
    sys.modules["com.sun.star.beans"] = beans
    sys.modules["com.sun.star.awt"] = awt

    unohelper = types.ModuleType("unohelper")

    class Base:
        pass

    def system_path_to_file_url(path):
        return f"file://{path}"

    unohelper.Base = Base
    unohelper.systemPathToFileUrl = system_path_to_file_url
    sys.modules["unohelper"] = unohelper


class FakePropertySetInfo:
    def __init__(self, container: "FakeUserDefinedProperties"):
        self._container = container

    def hasPropertyByName(self, name):
        return name in self._container.values


class FakeUserDefinedProperties:
    """Genügend Verhalten von `XPropertyContainer`/`XPropertySet` nachgebaut,
    um `settings_store.py`s `get_linked_document`/`set_linked_document`/
    `clear_linked_document` echt auszuführen."""

    def __init__(self):
        self.values = {}

    def getPropertySetInfo(self):
        return FakePropertySetInfo(self)

    def getPropertyValue(self, name):
        return self.values.get(name)

    def setPropertyValue(self, name, value):
        self.values[name] = value

    def addProperty(self, name, _attributes, value):
        self.values[name] = value

    def removeProperty(self, name):
        self.values.pop(name, None)


class FakeDocumentProperties:
    def __init__(self):
        self.UserDefinedProperties = FakeUserDefinedProperties()


class FakeDocument:
    def __init__(self):
        self._doc_properties = FakeDocumentProperties()

    def getDocumentProperties(self):
        return self._doc_properties
