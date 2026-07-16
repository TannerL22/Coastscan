"""Create tiny, deterministic, clearly synthetic Phase 1 demo inputs."""

from pathlib import Path

import geopandas as gpd
import numpy as np
import rasterio
from rasterio.transform import from_origin
from shapely.geometry import box


def create(root: Path) -> tuple[Path, Path]:
    output = root / "data" / "fixtures" / "synthetic_demo"
    output.mkdir(parents=True, exist_ok=True)
    land_path = output / "land.geojson"
    dem_path = output / "dem.tif"
    gpd.GeoDataFrame(
        {"fixture": ["synthetic_rectangular_island"]},
        geometry=[box(0, 0, 1000, 500)],
        crs="EPSG:3857",
    ).to_file(land_path, driver="GeoJSON")
    pixel = 5.0
    transform = from_origin(-250, 750, pixel, pixel)
    rows, columns = 200, 300
    y_centres = 750 - (np.arange(rows) + 0.5) * pixel
    elevation = np.repeat(y_centres[:, None], columns, axis=1).astype("float32")
    with rasterio.open(
        dem_path,
        "w",
        driver="GTiff",
        height=rows,
        width=columns,
        count=1,
        dtype="float32",
        crs="EPSG:3857",
        transform=transform,
        nodata=-9999.0,
    ) as dataset:
        dataset.write(elevation, 1)
    return land_path, dem_path


if __name__ == "__main__":
    created = create(Path(__file__).resolve().parents[1])
    print("Created clearly synthetic fixtures:")
    for path in created:
        print(path)
