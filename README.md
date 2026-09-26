# NutriDulce

Aplicación web de tienda y gestión para un pequeño emprendimiento de repostería. La tienda, la API y la persistencia corren en un único servidor local, sin dependencias de terceros en el backend.

## Estructura

```text
NutriDulce/
├── backend/
│   └── server.py          # Servidor HTTP, API REST, reglas de negocio y autenticación
├── data/
│   └── nutridulce.sqlite3 # Base SQLite creada al iniciar (no se versiona)
├── frontend/
│   ├── index.html         # Tienda y punto de entrada del panel
│   ├── styles.css         # Diseño adaptable
│   ├── app.js             # Tienda, carrito y panel administrativo
│   └── assets/            # Logo y fotografías originales; placeholder SVG para fotos pendientes
├── .gitignore
└── README.md
```

## Tecnologías y arquitectura

- **Frontend:** HTML, CSS y JavaScript nativos; interfaz adaptable para celulares y escritorio, sin proceso de compilación.
- **Backend:** Python 3.10+ con `http.server`, API JSON y reglas de negocio. No requiere instalar paquetes.
- **Base de datos:** SQLite, claves foráneas activadas, modo WAL e índices para los listados por fecha.
- El servidor entrega la tienda y la API en el mismo origen. SQLite y la lógica de stock son independientes de la presentación y se pueden migrar posteriormente a PostgreSQL y otro servidor WSGI/ASGI.

### Esquema de datos

- `users`: usuario administrador y hash PBKDF2 de contraseña.
- `products`: catálogo, precio en guaraníes, disponibilidad, publicación y umbral de stock bajo.
- `customers`: datos de contacto únicos por teléfono.
- `payment_methods`: medios iniciales (Efectivo, Transferencia, Tarjeta).
- `orders` / `order_items`: pedido, código público y detalle con instantánea de precio/nombre.
- `sales` / `sale_items`: venta generada al confirmar el pedido, con su detalle histórico.
- `stock_movements`: registro de ingresos, ajustes, ventas y devoluciones.
- `expenses`: egresos con categoría, monto y fecha.

Los montos se almacenan como enteros en guaraníes. Las relaciones conservan los detalles históricos si se desactiva un producto. Los cambios de stock y la confirmación de la venta ocurren dentro de una transacción SQLite.

## Identidad visual e imágenes

El logo (`logo-nutridulce.jpg`) y las dos fotografías (`budin-frutos-marmolado.jpeg`, `pastafrola-guayaba.jpeg`) se copiaron desde los adjuntos sin recodificarlos. Las imágenes de ambos budines usan la fotografía compartida; la pastafrola de guayaba usa su fotografía. La pastafrola de dulce de leche y las galletitas muestran un placeholder que indica que falta la fotografía. En el editor de productos se puede cambiar entre las fotografías disponibles y ese placeholder, o subir una nueva foto JPEG, PNG o WebP de hasta 5 MB. Los archivos que se suben se conservan originales; la tarjeta usa CSS para encuadrarlos.

## Inicio

Requiere Python 3.10 o superior.

```powershell
python backend/server.py
```

Abrir <http://127.0.0.1:8000>. La base se crea en `data/nutridulce.sqlite3` y se cargan los cinco productos iniciales la primera vez.

Acceso administrativo inicial: **admin / dulce123**. Definí `NUTRIDULCE_ADMIN_USER` y `NUTRIDULCE_ADMIN_PASSWORD` antes del primer inicio para elegir otras credenciales. El usuario administrador se crea solo cuando la tabla `users` está vacía. Para cambiar una contraseña después de la inicialización, se debe migrar/actualizar ese hash de acceso desde la base.

Variables opcionales:

| Variable | Predeterminado | Uso |
|---|---|---|
| `PORT` | `8000` | Puerto HTTP |
| `NUTRIDULCE_HOST` | `127.0.0.1` | Interfaz de red de escucha |
| `NUTRIDULCE_DB` | `data/nutridulce.sqlite3` | Ruta de la base SQLite |
| `NUTRIDULCE_ADMIN_USER` | `admin` | Usuario creado al inicializar |
| `NUTRIDULCE_ADMIN_PASSWORD` | `dulce123` | Contraseña creada al inicializar |

## Uso

El cliente agrega productos y envía un pedido con sus datos de contacto y entrega. El pedido queda **Pendiente** y no consume stock todavía. Al cambiarlo a **Confirmado**, el sistema vuelve a comprobar la disponibilidad, descuenta las cantidades, crea la venta y registra los movimientos de stock atómicamente. Al cancelar posteriormente una venta confirmada, se reintegra el stock y la venta queda marcada como cancelada. Los datos de productos históricos quedan guardados en los detalles del pedido/venta.

La administración permite gestionar pedidos, productos, reposición de stock, ventas filtradas, clientes y gastos; el resumen muestra ingresos diarios/semanales/mensuales, egresos, ganancia estimada, pagos, productos destacados y alertas de stock.

## API principal

- `GET /api/products`, `POST /api/orders`
- `POST /api/admin/login`, `POST /api/admin/logout`, `GET /api/admin/me`
- `GET|POST /api/admin/products`, `PUT|DELETE /api/admin/products/{id}`
- `GET /api/admin/orders`, `PATCH /api/admin/orders/{id}`
- `GET /api/admin/sales`, `GET /api/admin/customers`, `GET /api/admin/dashboard`
- `GET /api/admin/stock`, `POST /api/admin/stock/{product_id}`, `GET /api/admin/stock/movements`
- `GET|POST /api/admin/expenses`, `PUT|DELETE /api/admin/expenses/{id}`
