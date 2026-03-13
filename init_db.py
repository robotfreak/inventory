import sqlite3

connection = sqlite3.connect('inventory.db')

with open('schema.sql') as f:
    connection.executescript(f.read())

cur = connection.cursor()

# Kategorien anlegen
cur.execute("INSERT INTO categories (name, parent_id) VALUES (?, ?)", ('Elektronik', None)) # ID 1
cur.execute("INSERT INTO categories (name, parent_id) VALUES (?, ?)", ('Widerstände', 1)) # ID 2
cur.execute("INSERT INTO categories (name, parent_id) VALUES (?, ?)", ('Mikrocontroller', 1)) # ID 3
cur.execute("INSERT INTO categories (name, parent_id) VALUES (?, ?)", ('Werkzeug', None)) # ID 4

# Orte anlegen (locations)
cur.execute("INSERT INTO locations (name, description) VALUES (?, ?)", ('Sortimentsbox 1 (Gelb)', 'Im Regal oben links')) # ID 1
cur.execute("INSERT INTO locations (name, description) VALUES (?, ?)", ('Kiste Rot', 'Unterm Tisch')) # ID 2
cur.execute("INSERT INTO locations (name, description) VALUES (?, ?)", ('Wandhalterung', 'Werkstattwand')) # ID 3

# Items anlegen (mit location_id und sub_location)
cur.execute("INSERT INTO items (name, category_id, location_id, sub_location, quantity, min_quantity) VALUES (?, ?, ?, ?, ?, ?)",
            ('Raspberry Pi 5', 3, 2, 'Oben drauf', 1, 2)
            )

cur.execute("INSERT INTO items (name, category_id, location_id, sub_location, quantity, min_quantity) VALUES (?, ?, ?, ?, ?, ?)",
            ('Widerstand 10k', 2, 1, 'Fach 12', 95, 20)
            )
            
cur.execute("INSERT INTO items (name, category_id, location_id, sub_location, quantity, min_quantity) VALUES (?, ?, ?, ?, ?, ?)",
            ('Lötkolben', 4, 3, '', 1, 1)
            )

connection.commit()
connection.close()
