import duckdb

schools = r"C:\Users\PRECIOUS PRAISE\Desktop\GOESPATIAL ASSIGNMENTS\COM413_4-1-RetrievalData\mw_schools.shp"
districts = r"C:\Users\PRECIOUS PRAISE\Desktop\GOESPATIAL ASSIGNMENTS\COM413_4-1-RetrievalData\mw_districts.shp"

con = duckdb.connect()
con.execute("LOAD spatial;")
con.execute("SET geometry_always_xy = true;")

print("=== Dedza TAs ===")
rows = con.execute(
    """
    SELECT ta_name, district, region,
           ST_XMin(geom), ST_YMin(geom), ST_XMax(geom), ST_YMax(geom)
    FROM ST_Read(?)
    WHERE upper(district) LIKE '%DEDZA%'
    ORDER BY ta_name
    """,
    [districts],
).fetchall()
print("n", len(rows))
for r in rows:
    print(r)

print("\n=== Dedza schools by district field ===")
print(con.execute(
    """
    SELECT district, COUNT(*)
    FROM ST_Read(?)
    WHERE lower(district) LIKE '%dedza%'
    GROUP BY 1
    """,
    [schools],
).fetchall())
print("sample schools", con.execute(
    """
    SELECT school_nam, status, district, xcoord, ycoord
    FROM ST_Read(?)
    WHERE lower(district) LIKE '%dedza%'
    LIMIT 8
    """,
    [schools],
).fetchall())
print("status", con.execute(
    """
    SELECT status, COUNT(*)
    FROM ST_Read(?)
    WHERE lower(district) LIKE '%dedza%'
    GROUP BY 1
    """,
    [schools],
).fetchall())
