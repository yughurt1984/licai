import psycopg
c = psycopg.connect('postgresql://postgres:840510aa@localhost:5432/allassets')
cur = c.cursor()
cur.execute("SELECT sub_assets_type, sub_assets_type_name, main_assets_type, assets_class, trade, currency "
            "FROM ledger.assets_classify WHERE assets_class='derivatives' ORDER BY sub_assets_type")
for r in cur.fetchall():
    print(r)
cur.close(); c.close()
