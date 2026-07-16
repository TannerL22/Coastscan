"""Domain exceptions with user-actionable failure semantics."""


class CoastScanError(Exception):
    """Base class for expected CoastScan failures."""


class ConfigurationError(CoastScanError):
    """Region configuration is absent or invalid."""


class MissingInputError(CoastScanError):
    """A mandatory local source file is unavailable."""


class InvalidGeometryError(CoastScanError):
    """Vector geometry violates the Phase 1 contract."""


class RasterValidationError(CoastScanError):
    """Elevation raster metadata or coverage is invalid."""


class OrientationError(CoastScanError):
    """Coast orientation processing cannot continue."""


class QualityThresholdError(CoastScanError):
    """A configured blocking quality threshold was exceeded."""
