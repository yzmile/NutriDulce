# NutriDulce

Aplicación web de tienda y gestión para un pequeño emprendimiento de repostería. La tienda, la API y la persistencia corren en un único servidor. El frontend usa HTML, CSS y JavaScript nativos; el backend usa Python y PostgreSQL.

## Estructura

```text
NutriDulce/
├── backend/
│   ├── server.py                    # Servidor HTTP, API, reglas de negocio y autenticación
│   └── migrate_sqlite_to_postgres.py # Importador de una sola vez desde SQLite
├── data/
│   └── nutridulce.sqlite3            # Base SQLite de origen, solo para importar datos
├── frontend/
│   ├── index.html
│   ├── styles.css
│   ├── app.js
│   └── assets/
├── requirements.txt
└── README.md
```

## Tecnologías y datos

- **Frontend:** HTML, CSS y JavaScript nativos, responsive y sin compilación. Esta migración no modifica el frontend.
- **Backend:** Python 3.10 o superior, `http.server` y Psycopg 3.
- **Base de datos:** PostgreSQL configurado mediante `DATABASE_URL`.
- **Esquema:** `users`, `payment_methods`, `products`, `customers`, `orders`, `order_items`, `sales`, `sale_items`, `stock_movements` y `expenses`. Incluye claves primarias identity, claves foráneas, restricciones e índices por fecha.
- Los montos siguen almacenándose como enteros en guaraníes. Las respuestas JSON y las rutas API existentes se mantienen.

## Prueba local desde una base vacía

Requisitos: Python 3.10+, PostgreSQL local y acceso a una base vacía. Desde PowerShell, en la carpeta del proyecto:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

Creá una base y un usuario PostgreSQL local. Si ya tenés PostgreSQL instalado, podés usar `psql`:

```sql
CREATE USER nutridulce WITH PASSWORD 'cambia-esta-clave';
CREATE DATABASE nutridulce OWNER nutridulce;
```

Configurá la conexión, el usuario administrador y su contraseña antes del primer inicio:

```powershell
$env:DATABASE_URL = "postgresql://nutridulce:cambia-esta-clave@localhost:5432/nutridulce"
$env:NUTRIDULCE_ADMIN_USER = "admin"
$env:NUTRIDULCE_ADMIN_PASSWORD = "elegi-una-clave-segura"
python backend/server.py
```

Abrí <http://127.0.0.1:8000>. El servidor crea el esquema, los métodos de pago, el administrador y los cinco productos iniciales cuando la base está vacía. `PORT` se usa automáticamente si está definido; localmente el valor predeterminado es `8000`. En Render, el servidor escucha en `0.0.0.0` y usa el `PORT` asignado.

El administrador se crea únicamente si `users` está vacía. Cambiar esas variables después no reemplaza al usuario ya creado. En una importación, se conserva el administrador y su hash proveniente de SQLite.

### Usar PostgreSQL con Docker (opcional)

Si Docker está instalado, puede iniciar una base descartable para la prueba:

```powershell
docker run --name nutridulce-postgres -e POSTGRES_USER=nutridulce -e POSTGRES_PASSWORD=nutridulce-local -e POSTGRES_DB=nutridulce -p 5432:5432 -d postgres:17
$env:DATABASE_URL = "postgresql://nutridulce:nutridulce-local@localhost:5432/nutridulce"
$env:NUTRIDULCE_ADMIN_USER = "admin"
$env:NUTRIDULCE_ADMIN_PASSWORD = "elegi-una-clave-segura"
python backend/server.py
```

Si el puerto 5432 ya está ocupado, cambiá el primer puerto publicado (`-p 5433:5432`) y usá `localhost:5433` en `DATABASE_URL`. No ejecutes el servidor contra una base con información que quieras conservar para una prueba de inicio vacío.

## Importar la base SQLite existente

El importador lee `data/nutridulce.sqlite3` y copia las tablas y sus IDs a PostgreSQL; no borra ni modifica el archivo SQLite. Antes de ejecutarlo, configurá `DATABASE_URL` para que apunte a una base PostgreSQL vacía y asegurate de tener instaladas las dependencias (`python -m pip install -r requirements.txt`). Luego:

```powershell
$env:DATABASE_URL = "postgresql://nutridulce:tu-clave@localhost:5432/nutridulce"
python backend/migrate_sqlite_to_postgres.py
python backend/server.py
```

Para indicar otro archivo de origen, definí `SQLITE_PATH` antes de correr el importador. El importador se niega a copiar si encuentra datos en alguna tabla de destino, para evitar duplicados o sobrescrituras. La importación usa una transacción y avanza las secuencias de IDs al máximo importado. Conservá una copia de seguridad del archivo SQLite y verificá los conteos informados antes de cambiar el servicio de producción a la nueva conexión.

## Variables de entorno

| Variable | Predeterminado | Uso |
|---|---|---|
| `DATABASE_URL` | Sin valor; requerida | URL de conexión PostgreSQL |
| `PORT` | `8000` | Puerto HTTP (Render lo proporciona) |
| `NUTRIDULCE_HOST` | `0.0.0.0` | Interfaz de red de escucha |
| `NUTRIDULCE_ADMIN_USER` | `admin` | Usuario inicial, solo al crear el primer administrador |
| `NUTRIDULCE_ADMIN_PASSWORD` | `dulce123` | Contraseña inicial, solo al crear el primer administrador |
| `SQLITE_PATH` | `data/nutridulce.sqlite3` | Archivo de origen opcional del importador |

No publiques ni subas credenciales reales al repositorio. Configuralas como variables de entorno localmente y en la configuración del servicio de hosting.

## Render y PostgreSQL externo

Para conectar Render u otro hosting a PostgreSQL, guardá la URL de conexión como `DATABASE_URL` en las variables de entorno del servicio web. En Render, usá la URL interna si los dos servicios están en la misma región y mantené el comando de inicio:

```text
python backend/server.py
```

Este repositorio queda preparado para conectarse también a un PostgreSQL externo compatible mediante `DATABASE_URL`. Tené en cuenta que el plan gratuito actual de Render Postgres vence 30 días después de su creación; Render ofrece una ventana de 14 días para actualizarlo y el nivel gratuito no incluye backups. Por eso, el nivel gratuito no es almacenamiento permanente por sí solo. Consultá los límites vigentes de [Render Free](https://render.com/docs/free) y elegí un proveedor/plan con la duración y las copias de seguridad que necesitás.

## Funcionalidad existente

El cliente agrega productos y envía un pedido con sus datos de contacto y entrega. El pedido queda **Pendiente** y no consume stock todavía. Al cambiarlo a **Confirmado**, el sistema vuelve a comprobar la disponibilidad, descuenta las cantidades, crea la venta y registra los movimientos de stock dentro de una transacción. Al cancelar posteriormente una venta confirmada, se reintegra el stock y la venta queda marcada como cancelada. Los datos históricos de los productos permanecen en los detalles del pedido y la venta.

La administración permite gestionar pedidos, productos, reposición de stock, ventas filtradas, clientes y gastos. El resumen muestra ingresos diarios, semanales y mensuales, egresos, ganancia estimada, pagos, productos destacados y alertas de stock.

## API principal

- `GET /api/products`, `POST /api/orders`
- `POST /api/admin/login`, `POST /api/admin/logout`, `GET /api/admin/me`
- `GET|POST /api/admin/products`, `PUT|DELETE /api/admin/products/{id}`
- `GET /api/admin/orders`, `PATCH /api/admin/orders/{id}`
- `GET /api/admin/sales`, `GET /api/admin/customers`, `GET /api/admin/dashboard`
- `GET /api/admin/stock`, `POST /api/admin/stock/{product_id}`, `GET /api/admin/stock/movements`
- `GET|POST /api/admin/expenses`, `PUT|DELETE /api/admin/expenses/{id}`
