from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parents[1]
adm2 = ROOT / "data" / "raw" / "MWI_ADM2_simplified.geojson"
adm3 = ROOT / "data" / "raw" / "MWI_ADM3_simplified.geojson"

con = duckdb.connect()
con.execute("LOAD spatial;")
con.execute("SET geometry_always_xy = true;")

print(con.execute("DESCRIBE SELECT * FROM ST_Read(?)", [adm3]).fetchall())
print("sample", con.execute("SELECT * EXCLUDE geom FROM ST_Read(?) LIMIT 2", [adm3]).fetchall())

rows = con.execute(
    """
    SELECT a.shapeName,
           ST_Area(ST_Transform(ST_Intersection(a.geom, d.geom), 'EPSG:4326', 'EPSG:32736'))/1e6 AS km2
    FROM ST_Read(?) a, ST_Read(?) d
    WHERE lower(d.shapeName)='dedza'
      AND ST_Intersects(a.geom, d.geom)
      AND ST_Area(ST_Transform(ST_Intersection(a.geom, d.geom), 'EPSG:4326', 'EPSG:32736'))/1e6 > 5
    ORDER BY km2 DESC
    """,
    [adm3, adm2],
).fetchall()
print("n", len(rows))
for r in rows:
    print(r)
