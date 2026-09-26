"""NutriDulce API server. Uses only the Python standard library and SQLite."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import threading
import base64
import binascii
from datetime import date, datetime, timedelta
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = Path(os.environ.get("NUTRIDULCE_DB", ROOT / "data" / "nutridulce.sqlite3"))
WEB_ROOT = ROOT / "frontend"
SESSIONS: dict[str, str] = {}
DB_LOCK = threading.RLock()
PAYMENTS = {"Efectivo", "Transferencia", "Tarjeta"}
STATUSES = {"Pendiente", "Confirmado", "En preparación", "Listo para entregar", "Entregado", "Cancelado"}


def connect():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DB_PATH, timeout=15)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")
    db.execute("PRAGMA busy_timeout=15000")
    return db


def init_db():
    with connect() as db:
        db.executescript("""
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY, username TEXT UNIQUE NOT NULL, password_hash TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE IF NOT EXISTS payment_methods(id INTEGER PRIMARY KEY, name TEXT UNIQUE NOT NULL);
        CREATE TABLE IF NOT EXISTS products(id INTEGER PRIMARY KEY, name TEXT NOT NULL, description TEXT NOT NULL DEFAULT '', price INTEGER NOT NULL CHECK(price>=0), stock INTEGER NOT NULL DEFAULT 0 CHECK(stock>=0), image TEXT NOT NULL DEFAULT '', active INTEGER NOT NULL DEFAULT 1, low_stock_threshold INTEGER NOT NULL DEFAULT 5, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE IF NOT EXISTS customers(id INTEGER PRIMARY KEY, name TEXT NOT NULL, phone TEXT NOT NULL UNIQUE, address TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE IF NOT EXISTS orders(id INTEGER PRIMARY KEY, code TEXT UNIQUE NOT NULL, customer_id INTEGER NOT NULL REFERENCES customers(id), payment_method TEXT NOT NULL, delivery_address TEXT NOT NULL, notes TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'Pendiente', total INTEGER NOT NULL CHECK(total>=0), created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE IF NOT EXISTS order_items(id INTEGER PRIMARY KEY, order_id INTEGER NOT NULL REFERENCES orders(id), product_id INTEGER REFERENCES products(id) ON DELETE SET NULL, product_name TEXT NOT NULL, quantity INTEGER NOT NULL CHECK(quantity>0), unit_price INTEGER NOT NULL CHECK(unit_price>=0));
        CREATE TABLE IF NOT EXISTS sales(id INTEGER PRIMARY KEY, order_id INTEGER NOT NULL UNIQUE REFERENCES orders(id), customer_id INTEGER NOT NULL REFERENCES customers(id), payment_method TEXT NOT NULL, status TEXT NOT NULL, total INTEGER NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE IF NOT EXISTS sale_items(id INTEGER PRIMARY KEY, sale_id INTEGER NOT NULL REFERENCES sales(id), product_id INTEGER REFERENCES products(id) ON DELETE SET NULL, product_name TEXT NOT NULL, quantity INTEGER NOT NULL, unit_price INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS stock_movements(id INTEGER PRIMARY KEY, product_id INTEGER REFERENCES products(id) ON DELETE SET NULL, product_name TEXT NOT NULL, quantity_change INTEGER NOT NULL, reason TEXT NOT NULL, order_id INTEGER REFERENCES orders(id), created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE IF NOT EXISTS expenses(id INTEGER PRIMARY KEY, name TEXT NOT NULL, category TEXT NOT NULL, amount INTEGER NOT NULL CHECK(amount>=0), date TEXT NOT NULL, description TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
        CREATE INDEX IF NOT EXISTS idx_orders_created ON orders(created_at);
        CREATE INDEX IF NOT EXISTS idx_sales_created ON sales(created_at);
        CREATE INDEX IF NOT EXISTS idx_order_items_product ON order_items(product_id);
        """)
        for name in PAYMENTS:
            db.execute("INSERT OR IGNORE INTO payment_methods(name) VALUES (?)", (name,))
        if db.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0:
            salt = secrets.token_bytes(16)
            pwd = os.environ.get("NUTRIDULCE_ADMIN_PASSWORD", "dulce123")
            hashed = salt.hex() + ":" + hashlib.pbkdf2_hmac("sha256", pwd.encode(), salt, 240000).hex()
            db.execute("INSERT INTO users(username,password_hash) VALUES (?,?)", (os.environ.get("NUTRIDULCE_ADMIN_USER", "admin"), hashed))
        if db.execute("SELECT COUNT(*) FROM products").fetchone()[0] == 0:
            initial = [
                ("Budín de frutos secos", "Budín casero esponjoso con una mezcla generosa de frutos secos.", 10000, 12, "budin-frutos-marmolado", 5),
                ("Budín marmolado", "La combinación perfecta de vainilla y chocolate, hecho en casa.", 10000, 12, "budin-frutos-marmolado", 5),
                ("Pastafrola con dulce de leche", "Masa suave y dorada, rellena de dulce de leche.", 8000, 12, "photo-pending", 5),
                ("Pastafrola con dulce de guayaba", "Un clásico artesanal con dulce de guayaba paraguaya.", 8000, 12, "pastafrola-guayaba", 5),
                ("Galletitas de avena x3 unidades", "Tres galletitas de avena caseras, crocantes y deliciosas.", 10000, 15, "photo-pending", 5),
            ]
            db.executemany("INSERT INTO products(name,description,price,stock,image,low_stock_threshold) VALUES (?,?,?,?,?,?)", initial)
        # Move untouched starter images to the real customer photos and explicit photo placeholders.
        db.execute("UPDATE products SET image='budin-frutos-marmolado' WHERE name='Budín de frutos secos' AND image='cake'")
        db.execute("UPDATE products SET image='budin-frutos-marmolado' WHERE name='Budín marmolado' AND image='marble'")
        db.execute("UPDATE products SET image='photo-pending' WHERE name='Pastafrola con dulce de leche' AND image='pastafrola'")
        db.execute("UPDATE products SET image='pastafrola-guayaba' WHERE name='Pastafrola con dulce de guayaba' AND image='guava'")
        db.execute("UPDATE products SET image='photo-pending' WHERE name='Galletitas de avena x3 unidades' AND image='cookies'")


def serialize(row):
    return dict(row) if row is not None else None


def integer(value, label, minimum=0):
    try:
        n = int(value)
    except (TypeError, ValueError):
        raise ValueError(f"{label}: ingresá un número válido.")
    if n < minimum:
        raise ValueError(f"{label}: el valor mínimo es {minimum}.")
    return n


class Handler(BaseHTTPRequestHandler):
    server_version = "NutriDulce/1.0"

    def log_message(self, fmt, *args):
        print("%s - %s" % (self.address_string(), fmt % args))

    def send_json(self, data, status=200, headers=None):
        body = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        if headers:
            for k, v in headers.items(): self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def body(self):
        try:
            n = int(self.headers.get("Content-Length", "0"))
            limit = 7_000_000 if urlparse(self.path).path == "/api/admin/images" else 1_000_000
            if n > limit: raise ValueError("La solicitud es demasiado grande.")
            return json.loads(self.rfile.read(n) or b"{}")
        except json.JSONDecodeError:
            raise ValueError("El formato de los datos no es válido.")

    def admin(self):
        cookie = SimpleCookie(self.headers.get("Cookie", ""))
        sid = cookie.get("nd_session")
        user = SESSIONS.get(sid.value) if sid else None
        if not user:
            self.send_json({"error": "Iniciá sesión para continuar."}, 401)
            return False
        return True

    def do_GET(self):
        path = unquote(urlparse(self.path).path)
        query = {k: v[0] for k, v in parse_qs(urlparse(self.path).query).items()}
        try:
            with DB_LOCK, connect() as db:
                if path == "/api/products":
                    return self.send_json([serialize(r) for r in db.execute("SELECT * FROM products WHERE active=1 ORDER BY id")])
                if path == "/api/admin/me":
                    if not self.admin(): return
                    return self.send_json({"username": "admin"})
                if path.startswith("/api/admin/") and not self.admin(): return
                if path == "/api/admin/products":
                    return self.send_json([serialize(r) for r in db.execute("SELECT * FROM products ORDER BY id")])
                if path == "/api/admin/orders":
                    rows = db.execute("SELECT o.*,c.name customer_name,c.phone customer_phone,c.address customer_saved_address FROM orders o JOIN customers c ON c.id=o.customer_id ORDER BY o.created_at DESC")
                    result = [serialize(r) for r in rows]
                    for order in result:
                        order["items"] = [serialize(r) for r in db.execute("SELECT * FROM order_items WHERE order_id=?", (order["id"],))]
                    return self.send_json(result)
                if path == "/api/admin/sales":
                    sql = "SELECT s.*,c.name customer_name,c.phone customer_phone FROM sales s JOIN customers c ON c.id=s.customer_id WHERE 1=1"
                    args = []
                    if query.get("from"): sql += " AND date(s.created_at)>=?"; args.append(query["from"])
                    if query.get("to"): sql += " AND date(s.created_at)<=?"; args.append(query["to"])
                    if query.get("customer"): sql += " AND c.name LIKE ?"; args.append("%"+query["customer"]+"%")
                    if query.get("payment"): sql += " AND s.payment_method=?"; args.append(query["payment"])
                    if query.get("status"): sql += " AND s.status=?"; args.append(query["status"])
                    if query.get("product"): sql += " AND EXISTS(SELECT 1 FROM sale_items si WHERE si.sale_id=s.id AND si.product_name LIKE ?)"; args.append("%"+query["product"]+"%")
                    result = [serialize(r) for r in db.execute(sql+" ORDER BY s.created_at DESC", args)]
                    for sale in result: sale["items"] = [serialize(r) for r in db.execute("SELECT * FROM sale_items WHERE sale_id=?", (sale["id"],))]
                    return self.send_json(result)
                if path == "/api/admin/customers":
                    return self.send_json([serialize(r) for r in db.execute("""SELECT c.*,(SELECT COUNT(*) FROM orders o WHERE o.customer_id=c.id) order_count,(SELECT COALESCE(SUM(s.total),0) FROM sales s WHERE s.customer_id=c.id AND s.status!='Cancelado') total_spent FROM customers c ORDER BY c.name""")])
                customer_match = re.fullmatch(r"/api/admin/customers/(\d+)", path)
                if customer_match:
                    customer = db.execute("SELECT * FROM customers WHERE id=?", (int(customer_match.group(1)),)).fetchone()
                    if not customer: return self.send_json({"error":"Cliente no encontrado."},404)
                    result = serialize(customer)
                    result["orders"] = [serialize(r) for r in db.execute("SELECT * FROM orders WHERE customer_id=? ORDER BY created_at DESC", (customer["id"],))]
                    for order in result["orders"]:
                        order["items"] = [serialize(r) for r in db.execute("SELECT product_name,quantity,unit_price FROM order_items WHERE order_id=?", (order["id"],))]
                    return self.send_json(result)
                if path == "/api/admin/expenses":
                    return self.send_json([serialize(r) for r in db.execute("SELECT * FROM expenses ORDER BY date DESC,id DESC")])
                if path == "/api/admin/stock":
                    return self.send_json([serialize(r) for r in db.execute("SELECT id,name,stock,low_stock_threshold FROM products ORDER BY stock,id")])
                if path == "/api/admin/dashboard":
                    today = date.today(); week = today - timedelta(days=6); month = today.replace(day=1)
                    day_sum = db.execute("SELECT COALESCE(SUM(total),0) FROM sales WHERE status!='Cancelado' AND date(created_at)=?", (today.isoformat(),)).fetchone()[0]
                    week_sum = db.execute("SELECT COALESCE(SUM(total),0) FROM sales WHERE status!='Cancelado' AND date(created_at)>=?", (week.isoformat(),)).fetchone()[0]
                    month_sum = db.execute("SELECT COALESCE(SUM(total),0) FROM sales WHERE status!='Cancelado' AND date(created_at)>=?", (month.isoformat(),)).fetchone()[0]
                    total = db.execute("SELECT COALESCE(SUM(total),0) FROM sales WHERE status!='Cancelado'").fetchone()[0]
                    count = db.execute("SELECT COUNT(*) FROM orders").fetchone()[0]
                    expenses = db.execute("SELECT COALESCE(SUM(amount),0) FROM expenses").fetchone()[0]
                    top = [serialize(r) for r in db.execute("SELECT product_name name,SUM(quantity) quantity FROM sale_items si JOIN sales s ON s.id=si.sale_id WHERE s.status!='Cancelado' GROUP BY product_name ORDER BY quantity DESC LIMIT 5")]
                    low = [serialize(r) for r in db.execute("SELECT id,name,stock,low_stock_threshold FROM products WHERE active=1 AND stock<=low_stock_threshold ORDER BY stock")]
                    payments = [serialize(r) for r in db.execute("SELECT payment_method name,COUNT(*) count,COALESCE(SUM(total),0) total FROM sales WHERE status!='Cancelado' GROUP BY payment_method")]
                    chart = []
                    for i in range(6,-1,-1):
                        d = today-timedelta(days=i)
                        amount = db.execute("SELECT COALESCE(SUM(total),0) FROM sales WHERE status!='Cancelado' AND date(created_at)=?",(d.isoformat(),)).fetchone()[0]
                        chart.append({"date":d.isoformat(),"label":d.strftime("%d/%m"),"total":amount})
                    return self.send_json({"today":day_sum,"week":week_sum,"month":month_sum,"total":total,"orders":count,"expenses":expenses,"profit":total-expenses,"top_products":top,"low_stock":low,"payments":payments,"chart":chart})
                if path == "/api/admin/stock/movements":
                    return self.send_json([serialize(r) for r in db.execute("SELECT * FROM stock_movements ORDER BY created_at DESC LIMIT 100")])
            if path == "/" or path.startswith("/"):
                target = (WEB_ROOT / ("index.html" if path == "/" else path.lstrip("/"))).resolve()
                if not str(target).startswith(str(WEB_ROOT.resolve())) or not target.is_file(): return self.send_error(404)
                content_type = {".html":"text/html; charset=utf-8",".css":"text/css; charset=utf-8",".js":"text/javascript; charset=utf-8",".svg":"image/svg+xml",".jpg":"image/jpeg",".jpeg":"image/jpeg",".png":"image/png",".webp":"image/webp"}.get(target.suffix,"application/octet-stream")
                data = target.read_bytes(); self.send_response(200); self.send_header("Content-Type",content_type); self.send_header("Content-Length",str(len(data))); self.end_headers(); self.wfile.write(data); return
        except (ValueError, sqlite3.Error) as exc: return self.send_json({"error":str(exc)},400)
        return self.send_json({"error":"No encontrado."},404)

    def do_POST(self): self.dispatch_mutation("POST")
    def do_PUT(self): self.dispatch_mutation("PUT")
    def do_PATCH(self): self.dispatch_mutation("PATCH")
    def do_DELETE(self): self.dispatch_mutation("DELETE")

    def dispatch_mutation(self, method):
        path = unquote(urlparse(self.path).path)
        try:
            data = self.body() if method not in ("DELETE",) else {}
            with DB_LOCK, connect() as db:
                if path == "/api/admin/login" and method == "POST":
                    username = str(data.get("username", "")).strip()
                    row = db.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
                    valid = False
                    if row:
                        salt, expected = row["password_hash"].split(":", 1)
                        actual = hashlib.pbkdf2_hmac("sha256", str(data.get("password", "")).encode(), bytes.fromhex(salt), 240000).hex()
                        valid = hmac.compare_digest(actual, expected)
                    if not valid: return self.send_json({"error":"Usuario o contraseña incorrectos."},401)
                    sid = secrets.token_urlsafe(32); SESSIONS[sid] = username
                    return self.send_json({"ok":True,"username":username},headers={"Set-Cookie":f"nd_session={sid}; HttpOnly; SameSite=Lax; Path=/; Max-Age=43200"})
                if path == "/api/admin/logout" and method == "POST":
                    cookie = SimpleCookie(self.headers.get("Cookie", "")); sid=cookie.get("nd_session")
                    if sid: SESSIONS.pop(sid.value,None)
                    return self.send_json({"ok":True},headers={"Set-Cookie":"nd_session=; HttpOnly; SameSite=Lax; Path=/; Max-Age=0"})
                if path.startswith("/api/admin/") and not self.admin(): return
                if path == "/api/orders" and method == "POST":
                    name=str(data.get("name","")).strip(); phone=str(data.get("phone","")).strip(); address=str(data.get("address","")).strip(); payment=data.get("payment"); notes=str(data.get("notes","")).strip()
                    if not name or len(name)>100: raise ValueError("Ingresá el nombre del cliente (hasta 100 caracteres).")
                    if not re.fullmatch(r"[+0-9()\s-]{7,25}",phone): raise ValueError("Ingresá un número de teléfono válido.")
                    if not address or len(address)>250: raise ValueError("Ingresá una dirección o lugar de entrega.")
                    if payment not in PAYMENTS: raise ValueError("Seleccioná un método de pago válido.")
                    items=data.get("items")
                    if not isinstance(items,list) or not items: raise ValueError("El carrito está vacío.")
                    merged={}
                    for item in items:
                        pid=integer(item.get("product_id"),"Producto",1); qty=integer(item.get("quantity"),"Cantidad",1)
                        merged[pid]=merged.get(pid,0)+qty
                    lines=[]; total=0
                    for pid,qty in merged.items():
                        product=db.execute("SELECT * FROM products WHERE id=? AND active=1",(pid,)).fetchone()
                        if not product: raise ValueError("Uno de los productos ya no está disponible. Actualizá el catálogo.")
                        if product["stock"]<qty: raise ValueError(f"Stock insuficiente para {product['name']}.")
                        lines.append((product,qty)); total+=product["price"]*qty
                    db.execute("INSERT INTO customers(name,phone,address) VALUES (?,?,?) ON CONFLICT(phone) DO UPDATE SET name=excluded.name,address=excluded.address",(name,phone,address))
                    customer=db.execute("SELECT id FROM customers WHERE phone=?",(phone,)).fetchone()[0]
                    code="ND-"+datetime.now().strftime("%y%m%d")+"-"+secrets.token_hex(3).upper()
                    cursor=db.execute("INSERT INTO orders(code,customer_id,payment_method,delivery_address,notes,total) VALUES (?,?,?,?,?,?)",(code,customer,payment,address,notes,total))
                    for product,qty in lines: db.execute("INSERT INTO order_items(order_id,product_id,product_name,quantity,unit_price) VALUES (?,?,?,?,?)",(cursor.lastrowid,product["id"],product["name"],qty,product["price"]))
                    return self.send_json({"ok":True,"code":code,"total":total,"message":"¡Pedido recibido! Te contactaremos para confirmar los detalles."},201)
                if path == "/api/admin/products" and method == "POST":
                    name=str(data.get("name","")).strip(); description=str(data.get("description","")).strip(); image=str(data.get("image","cake")).strip() or "cake"
                    if not name or len(name)>120: raise ValueError("El nombre del producto es obligatorio (hasta 120 caracteres).")
                    price=integer(data.get("price"),"Precio"); stock=integer(data.get("stock"),"Stock"); threshold=integer(data.get("low_stock_threshold",5),"Aviso de poco stock")
                    cur=db.execute("INSERT INTO products(name,description,price,stock,image,low_stock_threshold,active) VALUES (?,?,?,?,?,?,?)",(name,description,price,stock,image,threshold,int(bool(data.get("active",True)))))
                    if stock: db.execute("INSERT INTO stock_movements(product_id,product_name,quantity_change,reason) VALUES (?,?,?,'Stock inicial')",(cur.lastrowid,name,stock))
                    return self.send_json({"ok":True,"id":cur.lastrowid},201)
                if path == "/api/admin/expenses" and method == "POST":
                    name=str(data.get("name","")).strip(); category=str(data.get("category","")).strip(); amount=integer(data.get("amount"),"Monto"); day=str(data.get("date",date.today().isoformat()))
                    if not name or not category: raise ValueError("Ingresá el nombre y la categoría del gasto.")
                    date.fromisoformat(day)
                    cur=db.execute("INSERT INTO expenses(name,category,amount,date,description) VALUES (?,?,?,?,?)",(name,category,amount,day,str(data.get("description","")).strip()))
                    return self.send_json({"ok":True,"id":cur.lastrowid},201)
                if path.startswith("/api/admin/"):
                    return self.admin_mutation(db,path,method,data)
        except (ValueError, sqlite3.IntegrityError, sqlite3.Error) as exc:
            return self.send_json({"error":str(exc)},400)
        return self.send_json({"error":"No encontrado."},404)

    def admin_mutation(self, db, path, method, data):
        if path == "/api/admin/images" and method == "POST":
            data_url = str(data.get("data", ""))
            image_match = re.fullmatch(r"data:(image/(?:jpeg|png|webp));base64,([A-Za-z0-9+/=]+)", data_url)
            if not image_match: raise ValueError("Elegí una imagen JPEG, PNG o WebP.")
            mime, payload = image_match.groups()
            try: raw = base64.b64decode(payload, validate=True)
            except (binascii.Error, ValueError): raise ValueError("El archivo de imagen no es válido.")
            if not raw or len(raw) > 5_000_000: raise ValueError("La imagen debe pesar 5 MB o menos.")
            signatures = {"image/jpeg": raw.startswith(b"\xff\xd8\xff"), "image/png": raw.startswith(b"\x89PNG\r\n\x1a\n"), "image/webp": len(raw) >= 12 and raw.startswith(b"RIFF") and raw[8:12] == b"WEBP"}
            if not signatures[mime]: raise ValueError("El contenido del archivo no coincide con su formato.")
            ext = {"image/jpeg":"jpg", "image/png":"png", "image/webp":"webp"}[mime]
            filename = f"product-{hashlib.sha256(raw).hexdigest()[:16]}.{ext}"
            (WEB_ROOT / "assets" / filename).write_bytes(raw)
            return self.send_json({"ok":True,"image":filename},201)
        match = re.fullmatch(r"/api/admin/products/(\d+)",path)
        if match:
            pid=int(match.group(1)); p=db.execute("SELECT * FROM products WHERE id=?",(pid,)).fetchone()
            if not p: return self.send_json({"error":"Producto no encontrado."},404)
            if method == "DELETE":
                db.execute("UPDATE products SET active=0,updated_at=CURRENT_TIMESTAMP WHERE id=?",(pid,)); return self.send_json({"ok":True})
            if method == "PUT":
                name=str(data.get("name","")).strip(); description=str(data.get("description","")).strip(); price=integer(data.get("price"),"Precio"); stock=integer(data.get("stock"),"Stock"); threshold=integer(data.get("low_stock_threshold",5),"Aviso de poco stock")
                if not name: raise ValueError("El nombre del producto es obligatorio.")
                if stock != p["stock"]: db.execute("INSERT INTO stock_movements(product_id,product_name,quantity_change,reason) VALUES (?,?,?,?)",(pid,name,stock-p["stock"],"Ajuste de stock"))
                db.execute("UPDATE products SET name=?,description=?,price=?,stock=?,image=?,active=?,low_stock_threshold=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",(name,description,price,stock,str(data.get("image","cake")),int(bool(data.get("active",True))),threshold,pid))
                return self.send_json({"ok":True})
        match=re.fullmatch(r"/api/admin/orders/(\d+)",path)
        if match and method == "PATCH":
            oid=int(match.group(1)); order=db.execute("SELECT * FROM orders WHERE id=?",(oid,)).fetchone(); status=data.get("status")
            if not order: return self.send_json({"error":"Pedido no encontrado."},404)
            if status not in STATUSES: raise ValueError("El estado indicado no es válido.")
            if order["status"]=="Cancelado": raise ValueError("Un pedido cancelado no se puede reabrir.")
            allowed_next = {
                "Pendiente": {"Confirmado", "Cancelado"},
                "Confirmado": {"En preparación", "Cancelado"},
                "En preparación": {"Listo para entregar", "Cancelado"},
                "Listo para entregar": {"Entregado", "Cancelado"},
                "Entregado": set(),
            }
            if status == order["status"]:
                return self.send_json({"ok": True, "message": "El pedido ya tenía ese estado."})
            if status not in allowed_next[order["status"]]:
                raise ValueError("Ese cambio de estado no está permitido.")
            if status=="Confirmado" and order["status"]=="Pendiente":
                items=db.execute("SELECT * FROM order_items WHERE order_id=?",(oid,)).fetchall()
                for item in items:
                    if item["product_id"] is not None:
                        product=db.execute("SELECT * FROM products WHERE id=?",(item["product_id"],)).fetchone()
                        if not product or product["stock"]<item["quantity"]: raise ValueError(f"Stock insuficiente para {item['product_name']}. Actualizá el stock para confirmar.")
                db.execute("UPDATE orders SET status=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",(status,oid))
                cur=db.execute("INSERT INTO sales(order_id,customer_id,payment_method,status,total) VALUES (?,?,?,?,?)",(oid,order["customer_id"],order["payment_method"],status,order["total"]))
                for item in items:
                    db.execute("INSERT INTO sale_items(sale_id,product_id,product_name,quantity,unit_price) VALUES (?,?,?,?,?)",(cur.lastrowid,item["product_id"],item["product_name"],item["quantity"],item["unit_price"]))
                    if item["product_id"] is not None:
                        db.execute("UPDATE products SET stock=stock-?,updated_at=CURRENT_TIMESTAMP WHERE id=?",(item["quantity"],item["product_id"]))
                        db.execute("INSERT INTO stock_movements(product_id,product_name,quantity_change,reason,order_id) VALUES (?,?,?,'Venta confirmada',?)",(item["product_id"],item["product_name"],-item["quantity"],oid))
                return self.send_json({"ok":True,"message":"Pedido confirmado; venta registrada y stock actualizado."})
            if status=="Cancelado" and order["status"]!="Pendiente":
                items=db.execute("SELECT * FROM order_items WHERE order_id=?",(oid,)).fetchall()
                for item in items:
                    if item["product_id"] is not None:
                        db.execute("UPDATE products SET stock=stock+?,updated_at=CURRENT_TIMESTAMP WHERE id=?",(item["quantity"],item["product_id"]))
                        db.execute("INSERT INTO stock_movements(product_id,product_name,quantity_change,reason,order_id) VALUES (?,?,?,'Devolución por cancelación',?)",(item["product_id"],item["product_name"],item["quantity"],oid))
                db.execute("UPDATE sales SET status='Cancelado' WHERE order_id=?",(oid,))
            db.execute("UPDATE orders SET status=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",(status,oid))
            db.execute("UPDATE sales SET status=? WHERE order_id=?",(status,oid))
            return self.send_json({"ok":True,"message":"Estado del pedido actualizado."})
        match=re.fullmatch(r"/api/admin/expenses/(\d+)",path)
        if match:
            eid=int(match.group(1))
            if method=="DELETE": db.execute("DELETE FROM expenses WHERE id=?",(eid,)); return self.send_json({"ok":True})
            if method=="PUT":
                amount=integer(data.get("amount"),"Monto"); name=str(data.get("name","")).strip(); category=str(data.get("category","")).strip(); day=str(data.get("date",date.today().isoformat())); date.fromisoformat(day)
                if not name or not category: raise ValueError("Ingresá el nombre y la categoría del gasto.")
                db.execute("UPDATE expenses SET name=?,category=?,amount=?,date=?,description=? WHERE id=?",(name,category,amount,day,str(data.get("description","")).strip(),eid)); return self.send_json({"ok":True})
        match=re.fullmatch(r"/api/admin/stock/(\d+)",path)
        if match and method=="POST":
            pid=int(match.group(1)); quantity=integer(data.get("quantity"),"Cantidad",1); p=db.execute("SELECT * FROM products WHERE id=?",(pid,)).fetchone()
            if not p: return self.send_json({"error":"Producto no encontrado."},404)
            db.execute("UPDATE products SET stock=stock+?,updated_at=CURRENT_TIMESTAMP WHERE id=?",(quantity,pid))
            db.execute("INSERT INTO stock_movements(product_id,product_name,quantity_change,reason) VALUES (?,?,?,'Reposición manual')",(pid,p["name"],quantity))
            return self.send_json({"ok":True,"stock":p["stock"]+quantity})
        return self.send_json({"error":"No encontrado."},404)


if __name__ == "__main__":
    init_db()
    host=os.environ.get("NUTRIDULCE_HOST","0.0.0.0"); port=int(os.environ.get("PORT","8000"))
    print(f"NutriDulce disponible en http://{host}:{port} · Base de datos: {DB_PATH}")
    ThreadingHTTPServer((host,port),Handler).serve_forever()
