"""Dedza SDSS QGIS plugin entry point."""


def classFactory(iface):
    from .plugin import DedzaSdssPlugin

    return DedzaSdssPlugin(iface)
