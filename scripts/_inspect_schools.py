import duckdb

schools = r"C:\Users\PRECIOUS PRAISE\Desktop\GOESPATIAL ASSIGNMENTS\COM413_4-1-RetrievalData\mw_schools.shp"
districts = r"C:\Users\PRECIOUS PRAISE\Desktop\GOESPATIAL ASSIGNMENTS\COM413_4-1-RetrievalData\mw_districts.shp"

con = duckdb.connect()
con.execute("LOAD spatial;")
con.execute("SET geometry_always_xy = true;")

print("=== schools describe ===")
print(con.execute("DESCRIBE SELECT * FROM ST_Read(?)", [schools]).fetchall())
print("count", con.execute("SELECT COUNT(*) FROM ST_Read(?)", [schools]).fetchone())
print("sample", con.execute("SELECT * EXCLUDE geom FROM ST_Read(?) LIMIT 3", [schools]).fetchall())
cols = [r[0] for r in con.execute("DESCRIBE SELECT * FROM ST_Read(?)", [schools]).fetchall()]
print("cols", cols)

print("\n=== districts describe ===")
print(con.execute("DESCRIBE SELECT * FROM ST_Read(?)", [districts]).fetchall())
print(con.execute("SELECT * EXCLUDE geom FROM ST_Read(?)", [districts]).fetchall())
