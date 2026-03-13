import sqlite3
import os
import base64
import json
from flask import Flask, render_template, request, url_for, flash, redirect, send_file, jsonify
import qrcode
from io import BytesIO
from dotenv import load_dotenv
from PIL import Image, ImageDraw, ImageFont
from werkzeug.utils import secure_filename
import time
import requests
import threading

# Neues SDK
from google import genai
from google.genai import types

load_dotenv() # .env laden

UPLOAD_FOLDER = 'static/uploads'
if not os.path.exists(UPLOAD_FOLDER):
    os.makedirs(UPLOAD_FOLDER)

app = Flask(__name__)
app.config['SECRET_KEY'] = 'dev-key-1234'
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['DB_FOLDER'] = 'databases'

if not os.path.exists(app.config['DB_FOLDER']):
    os.makedirs(app.config['DB_FOLDER'])

def get_db_connection(db_path='inventory.db'):
    # Erlaubt dynamische Pfade (für externe DBs)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn

def get_all_databases():
    """Gibt eine Liste aller verfügbaren Datenbanken zurück."""
    dbs = [{'id': 'local', 'name': '🏠 Hauptlager (Server)', 'path': 'inventory.db'}]
    
    # Suche im DB-Ordner nach weiteren .db Dateien
    for f in os.listdir(app.config['DB_FOLDER']):
        if f.endswith('.db'):
            # Name aus Dateiname ableiten (z.B. peter.db -> Peter)
            name = f.replace('.db', '').replace('_', ' ').title()
            # Optional: Man könnte Metadaten in einer JSON speichern für schönere Namen
            dbs.append({
                'id': f,
                'name': f"📦 {name}",
                'path': os.path.join(app.config['DB_FOLDER'], f)
            })
    return dbs

def get_categories(conn):
    return conn.execute('SELECT * FROM categories ORDER BY name').fetchall()

def get_locations(conn):
    # Einfache Liste aller Locations laden
    locs = conn.execute('SELECT * FROM locations ORDER BY name').fetchall()
    
    # Mapping ID -> Name aufbauen
    loc_map = {l['id']: l for l in locs}
    
    result = []
    for l in locs:
        # Pfad aufbauen (z.B. "Regal A > Box 3")
        path = l['name']
        parent_id = l['parent_id']
        # Rekursion vermeiden (max 5 Ebenen zur Sicherheit)
        depth = 0
        while parent_id and parent_id in loc_map and depth < 5:
            parent = loc_map[parent_id]
            path = parent['name'] + " > " + path
            parent_id = parent['parent_id']
            depth += 1
            
        # Wir modifizieren das dict nicht direkt, sondern bauen ein neues Objekt
        l_dict = dict(l)
        l_dict['full_path'] = path
        result.append(l_dict)
        
    # Sortieren nach vollem Pfad
    return sorted(result, key=lambda x: x['full_path'])

# --- WLED Logic ---
def wled_blink(ip, start, count):
    try:
        url = f"http://{ip}/json/state"
        # Segment 0 setzen: Rot, volle Helligkeit
        payload = {
            "seg": [
                {"id": 0, "start": start, "stop": start + count, "col": [[255, 0, 0]], "fx": 0, "sx": 128, "ix": 128, "bri": 255}
            ],
            "on": True,
            "bri": 255
        }
        print(f"💡 WLED {ip}: Flash LEDs {start}-{start+count}")
        requests.post(url, json=payload, timeout=2)
        
        time.sleep(5)
        
        # Aus
        requests.post(url, json={"on": False}, timeout=2)
    except Exception as e:
        print(f"❌ WLED Error: {e}")

@app.route('/databases', methods=['GET', 'POST'])
def databases():
    if request.method == 'POST':
        if 'db_file' in request.files:
            file = request.files['db_file']
            if file and file.filename.endswith('.db'):
                filename = secure_filename(file.filename)
                file.save(os.path.join(app.config['DB_FOLDER'], filename))
                flash(f'Datenbank {filename} hochgeladen!')
        elif 'delete' in request.form:
            filename = request.form['delete']
            path = os.path.join(app.config['DB_FOLDER'], filename)
            if os.path.exists(path):
                os.remove(path)
                flash(f'Datenbank {filename} gelöscht.')
                
    dbs = get_all_databases()
    # Lokale DB rausfiltern für die Verwaltung (man soll sich nicht selbst löschen)
    managed_dbs = [d for d in dbs if d['id'] != 'local']
    
    return render_template('databases.html', databases=managed_dbs)

@app.route('/api/flash/<int:location_id>', methods=['POST'])
def api_flash(location_id):
    conn = get_db_connection()
    loc = conn.execute('SELECT * FROM locations WHERE id = ?', (location_id,)).fetchone()
    conn.close()
    
    if not loc: return jsonify({'success': False, 'msg': 'Ort unbekannt'})
    if not loc['wled_ip']: return jsonify({'success': False, 'msg': 'Kein WLED'})
    
    # Default Werte
    idx = loc['led_index'] or 0
    cnt = loc['led_count'] or 10
    
    threading.Thread(target=wled_blink, args=(loc['wled_ip'], idx, cnt)).start()
    return jsonify({'success': True})

# --- Gemini Vision V2 ---
def identify_image_with_ai(image_data):
    api_key = os.getenv('GOOGLE_API_KEY')
    if not api_key:
        return {"error": "API Key fehlt in .env"}
    
    model_name = 'gemini-2.5-flash'
    try:
        client = genai.Client(api_key=api_key)
        
        prompt = """
        Analyze this image of an electronic component.
        Return ONLY a JSON object with these keys:
        - name: Technical name (e.g. 'Resistor 10k 1/4W')
        - description: Brief details (markings, etc.)
        - quantity: Estimated count (integer, default 1)
        - category_hint: Category name (e.g. 'Resistors', 'Microcontrollers')
        """
        
        img = Image.open(BytesIO(image_data))
        
        response = client.models.generate_content(
            model=model_name,
            contents=[prompt, img],
            config=types.GenerateContentConfig(
                response_mime_type='application/json'
            )
        )
        
        if not response.text:
             return {"error": "Leere Antwort von AI"}
             
        data = json.loads(response.text)
        return data
        
    except Exception as e:
        return {"error": f"AI Error mit Modell {model_name}: {str(e)}"}

@app.route('/')
def index():
    # Neue Parameter
    current_db = request.args.get('db', 'local') # Default: Hauptlager
    
    # DB Liste laden
    all_dbs = get_all_databases()
    # Prüfen, ob gewählte DB existiert
    active_db_info = next((d for d in all_dbs if d['id'] == current_db), all_dbs[0])

    search = request.args.get('q', '')
    category_id = request.args.get('category_id', '')
    location_id = request.args.get('location_id', '')
    low_stock = request.args.get('low_stock', '')
    sort_by = request.args.get('sort', 'location') # Default: nach Ort sortieren
    order = request.args.get('order', 'asc')

    # Query bauen (gilt für jede DB)
    query = """
    SELECT i.*, c.name as category_name, l.name as location_name, l.wled_ip 
    FROM items i
    LEFT JOIN categories c ON i.category_id = c.id
    LEFT JOIN locations l ON i.location_id = l.id
    WHERE 1=1
    """
    params = []
    
    if search:
        query += " AND (i.name LIKE ? OR i.sub_location LIKE ?)"
        params.extend([f'%{search}%', f'%{search}%'])
    if category_id and current_db != 'all': # Filter nur wenn nicht global
        query += " AND i.category_id = ?"
        params.append(category_id)
    if location_id and current_db != 'all': # Filter nur wenn nicht global
        query += " AND i.location_id = ?"
        params.append(location_id)
    if low_stock:
        query += " AND i.quantity <= i.min_quantity"

    # Sortierung (SQL String bauen)
    sort_sql = "l.name, i.sub_location, i.name" # Default fallback
    if sort_by == 'name': sort_sql = "i.name"
    elif sort_by == 'category': sort_sql = "c.name"
    elif sort_by == 'location': sort_sql = "l.name, i.sub_location"
    elif sort_by == 'quantity': sort_sql = "i.quantity"
    elif sort_by == 'sub_location': sort_sql = "i.sub_location"
    
    if order == 'desc': sort_sql += " DESC"
    else: sort_sql += " ASC"
    
    query += f" ORDER BY {sort_sql}"
    
    final_items = []
    
    # Entscheiden: Nur eine DB oder ALLE durchsuchen?
    dbs_to_query = []
    if current_db == 'all':
        dbs_to_query = all_dbs
    else:
        dbs_to_query = [active_db_info]
        
    # --- SUCHE AUSFÜHREN ---
    categories = []
    locations = []
    
    for db_info in dbs_to_query:
        try:
            conn = get_db_connection(db_info['path'])
            
            # Daten holen
            items = conn.execute(query, params).fetchall()
            
            # Zusatzinfos für Filter (nur von aktiver DB laden, sonst Chaos)
            if db_info['id'] == current_db or len(dbs_to_query) == 1:
                categories = get_categories(conn)
                # Locations mit Pfaden
                all_locs = get_locations(conn)
                locations = all_locs
                # Map für Pfad-Ersetzung
                loc_path_map = {l['id']: l['full_path'] for l in all_locs}
            else:
                # Bei globaler Suche brauchen wir trotzdem die Pfade für die Anzeige
                all_locs_temp = get_locations(conn)
                loc_path_map = {l['id']: l['full_path'] for l in all_locs_temp}

            # Items verarbeiten
            for item in items:
                i_dict = dict(item)
                # Pfad ersetzen
                if i_dict['location_id'] in loc_path_map:
                    i_dict['location_name'] = loc_path_map[i_dict['location_id']]
                
                # DB-Herkunft markieren
                i_dict['_db_id'] = db_info['id']
                i_dict['_db_name'] = db_info['name']
                
                final_items.append(i_dict)
                
            conn.close()
        except Exception as e:
            print(f"❌ Fehler beim Lesen von {db_info['name']}: {e}")
            # Fehlerhaftes DB überspringen, aber weitermachen
            continue

    # Wenn wir über ALLE gesucht haben, müssen wir das Ergebnis in Python sortieren,
    # da SQL nur pro DB sortiert hat.
    if len(dbs_to_query) > 1:
        reverse = (order == 'desc')
        # Mapping Sort-Key
        key_func = lambda x: (x['location_name'] or '', x['sub_location'] or '', x['name']) # Default
        if sort_by == 'name': key_func = lambda x: x['name'].lower()
        elif sort_by == 'quantity': key_func = lambda x: x['quantity']
        # ... weitere Sortierungen hier ergänzen bei Bedarf
        
        final_items.sort(key=key_func, reverse=reverse)

    return render_template('index.html', items=final_items, categories=categories, locations=locations,
                           search=search, current_cat=category_id, current_loc=location_id, low_stock=low_stock,
                           sort=sort_by, order=order, 
                           all_dbs=all_dbs, current_db=current_db, # Neu
                           now=time.time())

@app.route('/item/<int:id>')
def item_detail(id):
    db_id = request.args.get('db', 'local')
    
    # Pfad finden
    db_path = 'inventory.db'
    if db_id != 'local':
        path = os.path.join(app.config['DB_FOLDER'], db_id)
        if os.path.exists(path):
            db_path = path
        else:
            return "Datenbank nicht gefunden", 404

    conn = get_db_connection(db_path)
    # Join mit Locations und Categories
    query = """
    SELECT i.*, c.name as category_name, l.name as location_name, l.parent_id 
    FROM items i
    LEFT JOIN categories c ON i.category_id = c.id
    LEFT JOIN locations l ON i.location_id = l.id
    WHERE i.id = ?
    """
    item = conn.execute(query, (id,)).fetchone()
    
    # Für Pfad
    if item and item['location_id']:
        # Locations laden für Pfad
        all_locs = get_locations(conn) # Diese Funktion nutzt intern conn, also OK
        loc_map = {l['id']: l['full_path'] for l in all_locs}
        
        # Item erweitern (read-only dict -> neues dict)
        item_dict = dict(item)
        if item['location_id'] in loc_map:
            item_dict['location_name'] = loc_map[item['location_id']]
        item = item_dict # überschreiben

    conn.close()
    if not item: return "Item not found", 404
    return render_template('item_detail.html', item=item, db_id=db_id)

@app.route('/create', methods=('GET', 'POST'))
def create():
    conn = get_db_connection()
    categories = get_categories(conn)
    locations = get_locations(conn)
    
    if request.method == 'POST':
        name = request.form['name']
        category_id = request.form.get('category_id')
        location_id = request.form.get('location_id')
        sub_location = request.form.get('sub_location')
        quantity = request.form.get('quantity', 0)
        min_quantity = request.form.get('min_quantity', 0)
        notes = request.form.get('notes', '')
        
        datasheet = None
        datasheet2 = None
        image1 = None
        image2 = None

        if 'datasheet' in request.files:
            file = request.files['datasheet']
            if file and file.filename != '':
                filename = secure_filename(file.filename)
                if filename:
                    upload_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
                    file.save(upload_path)
                    datasheet = filename

        if 'datasheet2' in request.files:
            file = request.files['datasheet2']
            if file and file.filename != '':
                filename = secure_filename(file.filename)
                if filename:
                    upload_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
                    file.save(upload_path)
                    datasheet2 = filename

        if 'image1' in request.files:
            file = request.files['image1']
            if file and file.filename != '':
                filename = secure_filename(file.filename)
                if filename:
                    upload_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
                    file.save(upload_path)
                    image1 = filename
                    
        if 'image2' in request.files:
            file = request.files['image2']
            if file and file.filename != '':
                filename = secure_filename(file.filename)
                if filename:
                    upload_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
                    file.save(upload_path)
                    image2 = filename

        if not name:
            flash('Name ist Pflicht!')
        else:
            conn.execute('INSERT INTO items (name, category_id, location_id, sub_location, quantity, min_quantity, notes, datasheet, datasheet2, image1, image2) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
                         (name, category_id, location_id, sub_location, quantity, min_quantity, notes, datasheet, datasheet2, image1, image2))
            conn.commit()
            conn.close()
            return redirect(url_for('index'))
    
    conn.close()
    return render_template('create.html', categories=categories, locations=locations)

@app.route('/identify', methods=('GET', 'POST'))
def identify():
    if request.method == 'POST':
        if 'image' not in request.files:
            flash('Kein Bild ausgewählt')
            return redirect(request.url)
        file = request.files['image']
        if file.filename == '':
            flash('Kein Bild ausgewählt')
            return redirect(request.url)
        if file:
            image_data = file.read()
            result = identify_image_with_ai(image_data)
            
            if result and 'error' not in result:
                conn = get_db_connection()
                categories = get_categories(conn)
                locations = get_locations(conn)
                conn.close()
                return render_template('create.html', categories=categories, locations=locations, prefill=result)
            else:
                err = result.get('error') if result else "Unbekannter Fehler"
                flash(f'Fehler: {err}')
                
    return render_template('identify.html')

# NEU: QR Scan Route
@app.route('/scan_qr')
def scan_qr():
    return render_template('scan_qr.html')

@app.route('/item/<int:id>/edit', methods=('GET', 'POST'))
def edit(id):
    conn = get_db_connection()
    item = conn.execute('SELECT * FROM items WHERE id = ?', (id,)).fetchone()
    categories = get_categories(conn)
    locations = get_locations(conn)

    if request.method == 'POST':
        name = request.form['name']
        category_id = request.form.get('category_id')
        location_id = request.form.get('location_id')
        sub_location = request.form.get('sub_location')
        quantity = request.form.get('quantity')
        min_quantity = request.form.get('min_quantity')
        notes = request.form.get('notes', '')
        
        datasheet = item['datasheet']
        if 'datasheet' in request.files:
            file = request.files['datasheet']
            if file and file.filename != '':
                filename = secure_filename(file.filename)
                file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
                datasheet = filename

        # Support for new files
        datasheet2 = item['datasheet2'] if 'datasheet2' in item.keys() else None
        if 'datasheet2' in request.files:
            file = request.files['datasheet2']
            if file and file.filename != '':
                filename = secure_filename(file.filename)
                file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
                datasheet2 = filename

        image1 = item['image1'] if 'image1' in item.keys() else None
        if 'image1' in request.files:
            file = request.files['image1']
            if file and file.filename != '':
                filename = secure_filename(file.filename)
                file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
                image1 = filename

        image2 = item['image2'] if 'image2' in item.keys() else None
        if 'image2' in request.files:
            file = request.files['image2']
            if file and file.filename != '':
                filename = secure_filename(file.filename)
                file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
                image2 = filename

        conn.execute('UPDATE items SET name = ?, category_id = ?, location_id = ?, sub_location = ?, quantity = ?, min_quantity = ?, notes = ?, datasheet = ?, datasheet2 = ?, image1 = ?, image2 = ?'
                     ' WHERE id = ?',
                     (name, category_id, location_id, sub_location, quantity, min_quantity, notes, datasheet, datasheet2, image1, image2, id))
        conn.commit()
        conn.close()
        return redirect(url_for('item_detail', id=id))

    conn.close()
    return render_template('edit.html', item=item, categories=categories, locations=locations)

@app.route('/categories', methods=('GET', 'POST'))
def categories():
    conn = get_db_connection()
    if request.method == 'POST':
        name = request.form['name']
        parent_id = request.form.get('parent_id') or None
        if name:
            conn.execute('INSERT INTO categories (name, parent_id) VALUES (?, ?)', (name, parent_id))
            conn.commit()
            flash('Kategorie angelegt!')
    cats = conn.execute('SELECT c.*, p.name as parent_name FROM categories c LEFT JOIN categories p ON c.parent_id = p.id ORDER BY c.name').fetchall()
    conn.close()
    return render_template('categories.html', categories=cats)

@app.route('/locations', methods=('GET', 'POST'))
def locations():
    conn = get_db_connection()
    if request.method == 'POST':
        name = request.form['name']
        description = request.form.get('description', '')
        parent_id = request.form.get('parent_id') or None # Neu
        
        if name:
            conn.execute('INSERT INTO locations (name, description, parent_id) VALUES (?, ?, ?)', (name, description, parent_id))
            conn.commit()
            flash('Ort angelegt!')
            return redirect(url_for('locations')) # Redirect wichtig, um POST-Resubmit zu verhindern
            
    # Wir brauchen eine Liste mit counts UND pfaden.
    # Da get_locations() die Pfade baut, nutzen wir das und joinen den count dazu.
    
    # 1. Alle Locations mit Pfaden
    all_locs = get_locations(conn)
    
    # 2. Counts holen
    counts = conn.execute('SELECT location_id, COUNT(id) as cnt FROM items GROUP BY location_id').fetchall()
    count_map = {row['location_id']: row['cnt'] for row in counts}
    
    # 3. Mergen
    for l in all_locs:
        l['item_count'] = count_map.get(l['id'], 0)
        
    conn.close()
    return render_template('locations.html', locations=all_locs, now=time.time())

@app.route('/locations/<int:id>/edit', methods=('GET', 'POST'))
def edit_location(id):
    conn = get_db_connection()
    location = conn.execute('SELECT * FROM locations WHERE id = ?', (id,)).fetchone()
    all_locs = get_locations(conn)

    if request.method == 'POST':
        name = request.form['name']
        description = request.form.get('description', '')
        
        # WLED Fields
        wled_ip = request.form.get('wled_ip')
        led_index = request.form.get('led_index', 0)
        led_count = request.form.get('led_count', 10)
        parent_id = request.form.get('parent_id') or None
        
        # Rekursion vermeiden: nicht sich selbst als parent setzen
        if parent_id and int(parent_id) == id:
            parent_id = None

        if name:
            conn.execute('UPDATE locations SET name = ?, description = ?, wled_ip = ?, led_index = ?, led_count = ?, parent_id = ? WHERE id = ?',
                         (name, description, wled_ip, led_index, led_count, parent_id, id))
            conn.commit()
            flash('Ort aktualisiert!')
            conn.close()
            return redirect(url_for('locations'))

    conn.close()
    return render_template('edit_location.html', location=location, all_locations=all_locs)

# --- E-Reihen Generator ---
E_SERIES = {
    'E3': [10, 22, 47],
    'E6': [10, 15, 22, 33, 47, 68],
    'E12': [10, 12, 15, 18, 22, 27, 33, 39, 47, 56, 68, 82],
    'E24': [10, 11, 12, 13, 15, 16, 18, 20, 22, 24, 27, 30, 33, 36, 39, 43, 47, 51, 56, 62, 68, 75, 82, 91]
}
def generate_e_series_values(series_name, min_val, max_val):
    base_values = E_SERIES.get(series_name, [])
    values = []
    import math
    if min_val <= 0: min_val = 1 
    start_exp = int(math.floor(math.log10(min_val))) - 1
    end_exp = int(math.floor(math.log10(max_val))) + 1
    for exp in range(start_exp, end_exp + 1):
        multiplier = 10 ** exp
        for val in base_values:
            actual_val = val * multiplier / 10.0
            actual_val = float(f"{actual_val:.10g}")
            if actual_val >= min_val and actual_val <= max_val:
                values.append(actual_val)
    return sorted(list(set(values)))

def format_value(val, unit):
    if val >= 1000000: v_str = f"{val/1000000:g}M"
    elif val >= 1000: v_str = f"{val/1000:g}k"
    else: v_str = f"{val:g}"
    return f"{v_str}{unit}"

@app.route('/bulk_create', methods=('GET', 'POST'))
def bulk_create():
    conn = get_db_connection()
    categories = get_categories(conn)
    locations = get_locations(conn)
    if request.method == 'POST':
        name_prefix = request.form['name_prefix']
        category_id = request.form.get('category_id')
        location_id = request.form.get('location_id')
        series = request.form['series']
        min_val = float(request.form['min_val'])
        max_val = float(request.form['max_val'])
        unit = request.form['unit']
        sub_loc_base = request.form.get('sub_location_base', 'Fach ')
        start_index = int(request.form.get('start_index', 1))
        default_qty = int(request.form['default_qty'])
        default_min = int(request.form['default_min'])
        values = generate_e_series_values(series, min_val, max_val)
        count = 0
        for i, val in enumerate(values):
            val_str = format_value(val, unit)
            full_name = f"{name_prefix} {val_str}"
            if start_index > 0:
                current_idx = start_index + i
                sub_loc = f"{sub_loc_base}{current_idx}"
            else:
                sub_loc = sub_loc_base
            conn.execute('INSERT INTO items (name, category_id, location_id, sub_location, quantity, min_quantity) VALUES (?, ?, ?, ?, ?, ?)',
                         (full_name, category_id, location_id, sub_loc, default_qty, default_min))
            count += 1
        conn.commit()
        flash(f'{count} Teile erfolgreich in Location ID {location_id} angelegt!')
        conn.close()
        return redirect(url_for('index'))
    conn.close()
    return render_template('bulk_create.html', categories=categories, locations=locations, e_series=E_SERIES.keys())

@app.route('/print_label')
def print_label():
    location_id = request.args.get('location_id')
    size = request.args.get('size', '50x14')
    conn = get_db_connection()
    location = conn.execute('SELECT * FROM locations WHERE id = ?', (location_id,)).fetchone()
    conn.close()
    if not location:
        return "Location not found", 404
    search_url = f"inv_loc:{location_id}" # NEU: Kurzcode
    img = qrcode.make(search_url)
    buffered = BytesIO()
    img.save(buffered, format="PNG")
    img_str = base64.b64encode(buffered.getvalue()).decode()
    return render_template('print_label.html', location=location, qr_b64=img_str, size=size)

@app.route('/download_label')
def download_label():
    location_id = request.args.get('location_id')
    size_str = request.args.get('size', '50x14') 
    fmt = request.args.get('format', 'png').lower()
    
    conn = get_db_connection()
    location = conn.execute('SELECT * FROM locations WHERE id = ?', (location_id,)).fetchone()
    conn.close()
    
    if not location:
        return "Location not found", 404

    # Wir bauen das Label im Querformat (zum Lesen)
    # Am Ende rotieren wir es für den Drucker
    if size_str == '30x14':
        w_mm, h_mm = 30, 14
        max_font_size = 35
    else: # 50x14
        w_mm, h_mm = 50, 14
        max_font_size = 45
        
    dpi = 300 
    w_px = int(w_mm * dpi / 25.4)
    h_px = int(h_mm * dpi / 25.4)
    
    img = Image.new('RGB', (w_px, h_px), color='white')
    draw = ImageDraw.Draw(img)
    
    # QR Code links
    margin = 5
    qr_size = h_px - (margin * 2)
    
    search_url = f"inv_loc:{location_id}" # NEU: Kurzcode
    qr = qrcode.make(search_url)
    qr = qr.resize((qr_size, qr_size))
    img.paste(qr, (margin, margin))
    
    # Text rechts daneben
    text_x = margin + qr_size + 15
    text_w = w_px - text_x - margin
    
    name = location['name']
    
    try:
        font_path = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
        if not os.path.exists(font_path): font_path = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
    except:
        font_path = None

    # Dynamische Schriftgröße
    font_size = max_font_size
    font = None
    
    while font_size > 10:
        if font_path:
            font = ImageFont.truetype(font_path, font_size)
        else:
            font = ImageFont.load_default()
            break
            
        bbox = draw.textbbox((0, 0), name, font=font)
        text_width = bbox[2] - bbox[0]
        if text_width <= text_w:
            break
        font_size -= 2
        
    # ID Zeile
    id_font_size = 18
    if font_path:
        font_id = ImageFont.truetype(font_path, id_font_size)
    else:
        font_id = ImageFont.load_default()
    
    draw.text((text_x, margin), name, font=font, fill='black')
    draw.text((text_x, h_px - margin - id_font_size - 5), f"ID: {location['id']}", font=font_id, fill='black')
    
    img = img.rotate(90, expand=True)
    
    img_io = BytesIO()
    
    if fmt == 'pdf':
        img.save(img_io, 'PDF', resolution=float(dpi))
        mimetype = 'application/pdf'
        filename = f"label_{location_id}_{size_str}.pdf"
    else:
        img.save(img_io, 'PNG')
        mimetype = 'image/png'
        filename = f"label_{location_id}_{size_str}.png"
        
    img_io.seek(0)
    return send_file(img_io, mimetype=mimetype, as_attachment=True, download_name=filename)

@app.route('/<int:id>/qr')
def qr_code(id):
    data = f"inventory_item:{id}" # Hier lassen wir es erstmal so
    img = qrcode.make(data)
    img_io = BytesIO()
    img.save(img_io, 'PNG')
    img_io.seek(0)
    return send_file(img_io, mimetype='image/png')

if __name__ == '__main__':
    # SSL Support - Absolute paths are safer for systemd
    import os
    base_dir = os.path.dirname(os.path.abspath(__file__))
    cert_path = os.path.join(base_dir, 'cert.pem')
    key_path = os.path.join(base_dir, 'key.pem')
    
    if os.path.exists(cert_path) and os.path.exists(key_path):
        context = (cert_path, key_path)
        print(f"Starting with SSL context: {context}")
        app.run(host='0.0.0.0', port=5000, debug=True, ssl_context=context)
    else:
        print("SSL certs not found, falling back to HTTP")
        app.run(host='0.0.0.0', port=5000, debug=True)
