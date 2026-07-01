# Infraestructura Viva — Prototipo en Docker

Este contenedor reproduce localmente el flujo del prototipo (EC2/ECS + RDS) usando
Docker Compose: un contenedor con PostgreSQL (equivalente local a RDS) y un
contenedor con una API FastAPI que expone las 5 consultas SQL documentadas.

## 1. Estructura

```
docker-prototipo/
├── app.py              # API FastAPI con las 5 consultas
├── Dockerfile           # Imagen de la app
├── docker-compose.yml   # Orquesta app + base de datos
├── init.sql             # Esquema + datos de prueba (se carga solo la primera vez)
└── requirements.txt
```

## 2. Levantar el entorno

Desde la carpeta `docker-prototipo/`:

```bash
docker compose up --build
```

Esto:
1. Construye la imagen de la app (Python 3.12 + FastAPI + psycopg2).
2. Levanta Postgres 15 y carga automáticamente `init.sql` (tablas `clientes`,
   `pedidos`, `tickets_soporte` con datos de prueba).
3. Espera a que la base de datos esté lista (`healthcheck`) antes de iniciar la app.
4. Publica la API en `http://localhost:8000`.

Para correr en segundo plano: `docker compose up --build -d`
Para ver logs: `docker compose logs -f`
Para bajar todo: `docker compose down` (agrega `-v` si además quieres borrar los datos)

## 3. Probar las 5 consultas

Con el stack arriba, cada endpoint corre una de las consultas del prototipo:

| Endpoint | Consulta |
|---|---|
| `GET /clientes/corporativo` | Clientes por segmento |
| `GET /pedidos/por-cliente` | Pedidos por cliente (JOIN + conteo) |
| `GET /pedidos/ticket-promedio` | Ticket promedio de pedidos completados |
| `GET /tickets/alta-prioridad` | Tickets de alta prioridad sin resolver |
| `GET /pedidos/por-estado` | Distribución de pedidos por estado |

```bash
curl http://localhost:8000/clientes/corporativo
curl http://localhost:8000/pedidos/por-cliente
```

También puedes conectarte directo a la base con cualquier cliente PostgreSQL
(DBeaver, la extensión de PostgreSQL de VS Code, o `psql`):

```bash
psql -h localhost -p 5432 -U admin_acme -d acme_infraestructura_viva
# contraseña: changeme
```

## 4. Qué valida este contenedor

- Que el esquema probado en SQLiteOnline funciona igual en PostgreSQL real.
- Que la app se conecta a la base únicamente por variables de entorno
  (`DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER`, `DB_PASSWORD`), el mismo patrón
  que usarías contra RDS.
- Que las 5 consultas devuelven resultados correctos antes de tocar AWS.

## 5. De aquí a AWS (ECS Fargate)

Este mismo contenedor de la app es el que subirías como imagen a ECS Fargate,
sin cambiar una línea de código — solo cambian las variables de entorno,
que en RDS real apuntarían al endpoint gestionado:

```bash
# 1. Crear el repositorio en ECR
aws ecr create-repository --repository-name infraestructura-viva-app

# 2. Autenticar Docker contra ECR
aws ecr get-login-password --region us-east-1 \
  | docker login --username AWS --password-stdin <cuenta>.dkr.ecr.us-east-1.amazonaws.com

# 3. Etiquetar y subir la imagen
docker build -t infraestructura-viva-app .
docker tag infraestructura-viva-app:latest \
  <cuenta>.dkr.ecr.us-east-1.amazonaws.com/infraestructura-viva-app:latest
docker push <cuenta>.dkr.ecr.us-east-1.amazonaws.com/infraestructura-viva-app:latest

# 4. En ECS: crear una Task Definition en Fargate que use esta imagen,
#    con las variables DB_HOST/DB_USER/DB_PASSWORD apuntando al RDS real
#    (idealmente vía Secrets Manager, no como texto plano).
```

En este caso el contenedor `db` de `docker-compose.yml` no se despliega en AWS
— solo sirve para desarrollo local. En AWS, `DB_HOST` pasa a ser el endpoint
de la instancia RDS creada en el documento de arquitectura.

## Nota

Este código se validó por sintaxis (Python y SQL) pero no se ejecutó end-to-end
en este entorno porque no tiene Docker disponible. Antes de darlo por bueno,
corre `docker compose up --build` en tu máquina y confirma que los 5 endpoints
responden con datos.
