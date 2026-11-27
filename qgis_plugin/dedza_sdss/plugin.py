"""Load the project's existing spatial data into QGIS without changing it."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from qgis.PyQt.QtCore import QByteArray, QSettings, QVariant
from qgis.PyQt.QtWidgets import QAction, QFileDialog
from qgis.core import (
    Qgis,
    QgsFeature,
    QgsGeometry,
    QgsMessageLog,
    QgsProject,
    QgsRasterLayer,
    QgsPointXY,
    QgsReferencedRectangle,
    QgsVectorLayer,
    QgsField,
)


VECTOR_TABLES = (
    "boundary",
    "cells",
    "recommendations",
    "facilities",
    "distance_facilities",
    "mhfr_facilities",
    "schools",
    "settlements",
    "osm_places",
    "roads",
    "waterways",
)
DISPLAY_NAMES = {
    "boundary": "Dedza district boundary",
    "cells": "Planning cells",
    "recommendations": "Priority recommendations",
    "facilities": "Baseline health facilities",
    "distance_facilities": "Distance reference facilities",
    "mhfr_facilities": "Health registry facilities",
    "schools": "Schools",
    "settlements": "Settlements",
    "osm_places": "OSM places",
    "roads": "Road network",
    "waterways": "Waterways",
}


class DedzaSdssPlugin:
    """QGIS toolbar/menu action for loading all checked-in SDSS layers."""

    def __init__(self, iface):
        self.iface = iface
        self.action = None

    def initGui(self):
        self.action = QAction("Load Dedza SDSS layers", self.iface.mainWindow())
        self.action.setToolTip("Load all available Dedza SDSS vector and raster layers")
        self.action.triggered.connect(self.load_layers)
        self.iface.addPluginToMenu("&Dedza SDSS", self.action)
        self.iface.addToolBarIcon(self.action)

    def unload(self):
        if self.action is not None:
            self.iface.removePluginMenu("&Dedza SDSS", self.action)
            self.iface.removeToolBarIcon(self.action)

    @staticmethod
    def _project_root():
        # Development installs resolve the checkout directly. A copied profile
        # install asks once and remembers the selected checkout.
        for parent in Path(__file__).resolve().parents:
            if (parent / "data" / "processed" / "dedza.sqlite").is_file():
                return parent
        configured = QSettings().value("DedzaSDSS/projectRoot", "")
        candidate = Path(configured) if configured else None
        if candidate is not None and (candidate / "data" / "processed" / "dedza.sqlite").is_file():
            return candidate
        return None

    def load_layers(self):
        root = self._project_root()
        if root is None:
            selected = QFileDialog.getExistingDirectory(
                self.iface.mainWindow(),
                "Select the Dedza SDSS project folder",
                str(Path.home()),
            )
            if not selected:
                return
            root = Path(selected).resolve()
            QSettings().setValue("DedzaSDSS/projectRoot", str(root))
        project = QgsProject.instance()
        loaded = 0
        failures = []

        db_path = root / "data" / "processed" / "dedza.sqlite"
        if db_path.is_file():
            group = self._replace_group(project, "SDSS database")
            try:
                with sqlite3.connect(str(db_path)) as connection:
                    tables = {
                        row[0]
                        for row in connection.execute(
                            "SELECT name FROM sqlite_master WHERE type='table'"
                        )
                    }
                    for table in VECTOR_TABLES:
                        if table not in tables:
                            continue
                        try:
                            layer = self._database_layer(connection, table)
                            if layer.isValid():
                                project.addMapLayer(layer, False)
                                group.addLayer(layer)
                                loaded += 1
                            else:
                                failures.append("Database layer: " + table)
                        except Exception as error:
                            failures.append("{}: {}".format(table, error))
            except sqlite3.Error as error:
                failures.append("Database: {}".format(error))

        raw = root / "data" / "raw"
        if raw.is_dir():
            group = self._replace_group(project, "Source data")
            for path in sorted(raw.glob("*.geojson")):
                layer = QgsVectorLayer(str(path), path.stem.replace("_", " "), "ogr")
                if layer.isValid():
                    project.addMapLayer(layer, False)
                    group.addLayer(layer)
                    loaded += 1
                else:
                    failures.append("GeoJSON: {}".format(path.name))

        raster_paths = sorted((root / "data").rglob("*.tif"))
        if raster_paths:
            group = self._replace_group(project, "Raster layers")
            for path in raster_paths:
                layer = QgsRasterLayer(str(path), path.stem.replace("_", " "))
                if layer.isValid():
                    project.addMapLayer(layer, False)
                    group.addLayer(layer)
                    loaded += 1
                else:
                    failures.append("Raster: {}".format(path.name))

        self._zoom_to_dedza(project, db_path)
        message = "Loaded {} available layers.".format(loaded)
        level = Qgis.Success
        if failures:
            message += " {} source(s) could not be read; see the log.".format(len(failures))
            level = Qgis.Warning
            for failure in failures:
                QgsMessageLog.logMessage(failure, "Dedza SDSS", Qgis.Warning)
        if loaded == 0:
            message = "No layers found. Open the plugin from the project checkout that contains data/."
            level = Qgis.Warning
        self.iface.messageBar().pushMessage("Dedza SDSS", message, level=level, duration=8)

    @staticmethod
    def _replace_group(project, name):
        root = project.layerTreeRoot()
        old = root.findGroup(name)
        if old is not None:
            for child in old.findLayers():
                project.removeMapLayer(child.layerId())
            root.removeChildNode(old)
        return root.addGroup(name)

    @staticmethod
    def _qvariant_type(declared_type):
        kind = (declared_type or "").upper()
        if "INT" in kind:
            return QVariant.LongLong
        if any(token in kind for token in ("REAL", "FLOA", "DOUB", "NUM")):
            return QVariant.Double
        return QVariant.String

    @classmethod
    def _database_layer(cls, connection, table):
        cursor = connection.execute("PRAGMA table_info('{}')".format(table))
        columns = [(row[1], row[2]) for row in cursor.fetchall()]
        geometry_column = "geojson" if any(name == "geojson" for name, _ in columns) else None
        attribute_columns = [
            (name, declared) for name, declared in columns if name != geometry_column
        ]
        uri_type = "Unknown" if geometry_column else "Point"
        layer = QgsVectorLayer(uri_type + "?crs=EPSG:4326", DISPLAY_NAMES.get(table, table), "memory")
        provider = layer.dataProvider()
        provider.addAttributes([
            QgsField(name, cls._qvariant_type(declared))
            for name, declared in attribute_columns
        ])
        layer.updateFields()
        names = [name for name, _ in columns]
        features = []
        for row in connection.execute('SELECT * FROM "{}"'.format(table)):
            record = dict(zip(names, row))
            feature = QgsFeature(layer.fields())
            geometry_value = record.get(geometry_column) if geometry_column else None
            if geometry_value:
                geometry = json.loads(geometry_value)
                if geometry.get("type") == "Feature":
                    geometry = geometry.get("geometry")
                if geometry:
                    feature.setGeometry(QgsGeometry.fromJson(QByteArray(json.dumps(geometry).encode("utf-8"))))
            elif record.get("lon") is not None and record.get("lat") is not None:
                feature.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(float(record["lon"]), float(record["lat"]))))
            feature.setAttributes([record[name] for name, _ in attribute_columns])
            features.append(feature)
        provider.addFeatures(features)
        layer.updateExtents()
        return layer

    @staticmethod
    def _zoom_to_dedza(project, db_path):
        if not db_path.is_file():
            return
        try:
            with sqlite3.connect(str(db_path)) as connection:
                row = connection.execute("SELECT geojson FROM boundary LIMIT 1").fetchone()
            if not row or not row[0]:
                return
            boundary = QgsGeometry.fromJson(QByteArray(row[0].encode("utf-8")))
            rectangle = boundary.boundingBox()
            if not rectangle.isNull():
                rectangle.scale(1.08)
                project.viewSettings().setDefaultViewExtent(QgsReferencedRectangle(rectangle, project.crs()))
        except Exception:
            # Layer loading should still succeed if an older QGIS version lacks
            # the project view-settings API.
            return
