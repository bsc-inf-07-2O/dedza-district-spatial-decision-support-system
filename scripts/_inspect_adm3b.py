import duckdb, json
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
adm3 = ROOT / "data" / "raw" / "MWI_ADM3_simplified.geojson"
con = duckdb.connect()
con.execute("LOAD spatial;")
con.execute("SET geometry_always_xy = true;")

names = con.execute("SELECT shapeName, COUNT(*) FROM ST_Read(?) GROUP BY 1 ORDER BY 1", [adm3]).fetchall()
print("n unique", len(names), "n features", sum(n for _, n in names))
for n, c in names:
    if any(x in n.lower() for x in [
        "dedza", "tambala", "kachinda", "kachere", "kasumbu", "kaphuka",
        "chauma", "kamenya", "chiliku", "chitundu", "kanyama", "mlolo",
        "kamtuk", "pembere", "chafisi"
    ]):
        print("HIT", n, c)

print("\n--- centroids of reported intersects ---")
adm2 = ROOT / "data" / "raw" / "MWI_ADM2_simplified.geojson"
rows = con.execute(
    """
    SELECT a.shapeName, ST_X(ST_Centroid(a.geom)), ST_Y(ST_Centroid(a.geom)),
           ST_X(ST_Centroid(ST_Intersection(a.geom, d.geom))),
           ST_Y(ST_Centroid(ST_Intersection(a.geom, d.geom)))
    FROM ST_Read(?) a, ST_Read(?) d
    WHERE lower(d.shapeName)='dedza' AND ST_Intersects(a.geom, d.geom)
    ORDER BY a.shapeName
    """,
    [adm3, adm2],
).fetchall()
for r in rows:
    print(r)
