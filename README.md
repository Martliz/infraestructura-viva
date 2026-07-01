# \# Infraestructura Viva — Prototipo sobre Floci (AWS real, local)

# 

# Este prototipo levanta \*\*Floci\*\*, un emulador de AWS de código abierto (alternativa

# a LocalStack, mismo puerto 4566), y una API FastAPI que llama a los servicios de

# AWS \*\*con boto3 real\*\* — RDS, S3, DynamoDB, SNS, SQS y CloudWatch — exactamente

# como se llamarían contra AWS de verdad. Solo cambia el `endpoint\_url`.

# 

# No es una simulación con una base de datos genérica: `aws\_setup.py` ejecuta

# `create\_db\_instance`, `create\_bucket`, `create\_table`, `create\_topic`,

# `put\_metric\_alarm`, etc. contra la API real de AWS que expone Floci, y Floci

# responde levantando un contenedor real de PostgreSQL detrás para RDS.

# 

# \## 1. Estructura

# docker-prototipo/

# ├── app.py           # API FastAPI — endpoints por servicio AWS

# ├── aws\_setup.py      # Aprovisiona RDS / S3 / DynamoDB / SNS / SQS / CloudWatch vía boto3

# ├── init.sql           # Esquema + datos de prueba, aplicado sobre RDS

# ├── Dockerfile

# ├── docker-compose.yml # Servicios: floci + app

# └── requirements.txt

# 

# \## 2. Levantar el entorno

# 

# ```bash

# cd docker-prototipo

# docker compose up --build

# ```

# 

# `docker-compose.yml` levanta dos contenedores:

# 

# \- \*\*`floci`\*\*: el emulador de AWS, expuesto en `http://localhost:4566`. Necesita

# &#x20; acceso al socket de Docker (`/var/run/docker.sock`) porque RDS, Lambda, ECS y

# &#x20; otros servicios "reales" de Floci levantan contenedores Docker de verdad

# &#x20; detrás de la API.

# \- \*\*`app`\*\*: la API FastAPI. Al arrancar, ejecuta `aws\_setup.provision\_all()` en

# &#x20; el evento de startup, que:

# &#x20; 1. Espera a que Floci esté disponible.

# &#x20; 2. Crea los buckets S3, la tabla DynamoDB, el tema SNS y la cola SQS (con la

# &#x20;    suscripción SNS→SQS).

# &#x20; 3. Crea la instancia RDS PostgreSQL (`create\_db\_instance`) y espera a que

# &#x20;    Floci termine de levantar el contenedor real de Postgres detrás.

# &#x20; 4. Aplica `init.sql` sobre esa instancia.

# &#x20; 5. Crea las dos alarmas de CloudWatch del plan de monitoreo, conectadas al

# &#x20;    tema SNS.

# 

# La primera vez puede tardar 30-60 segundos (Floci tiene que descargar y

# levantar `postgres:16-alpine`). Sigue el progreso con `docker compose logs -f app`.

# 

# Para correr en segundo plano: `docker compose up --build -d`

# Para bajar todo: `docker compose down` (agrega `-v` para borrar también los

# volúmenes de datos)

# 

# \## 3. Endpoints

# 

# \### RDS (PostgreSQL real, vía Floci)

# | Endpoint | Qué hace |

# |---|---|

# | `GET /clientes/corporativo` | Consulta 1: clientes por segmento |

# | `GET /pedidos/por-cliente` | Consulta 2: pedidos por cliente (JOIN) |

# | `GET /pedidos/ticket-promedio` | Consulta 3: ticket promedio |

# | `GET /pedidos/por-estado` | Consulta 5: distribución de pedidos por estado |

# 

# \### DynamoDB

# | Endpoint | Qué hace |

# |---|---|

# | `GET /dynamodb/tickets` | Tickets de alta prioridad sin resolver (NoSQL) |

# 

# \### S3

# | Endpoint | Qué hace |

# |---|---|

# | `GET /s3/buckets` | Lista los buckets creados |

# 

# \### SNS / SQS

# | Endpoint | Qué hace |

# |---|---|

# | `POST /sns/notificar?asunto=...\&mensaje=...` | Publica una alerta manual en el tema SNS |

# | `GET /sqs/mensajes` | Lee (y borra) los mensajes que llegaron a la cola desde SNS |

# | `POST /sqs/purgar` | Vacía la cola por completo (útil para pruebas limpias) |

# 

# \### CloudWatch (plan de monitoreo)

# | Endpoint | Qué hace |

# |---|---|

# | `GET /cloudwatch/alarmas` | Estado actual de las 2 alarmas |

# | `POST /cloudwatch/metrica-cpu?valor=...` | Publica un dato de CPUUtilization |

# | `POST /cloudwatch/metrica-red?bytes\_in=...` | Publica un dato de NetworkIn |

# | `POST /cloudwatch/metrica-errores?cantidad=...` | Publica un dato de ErrorCount |

# | `POST /cloudwatch/simular-alarma?alarma=...\&estado=ALARM` | Fuerza el estado de una alarma y dispara la notificación SNS (ver nota abajo) |

# 

# \### General

# | Endpoint | Qué hace |

# |---|---|

# | `GET /` | Health check |

# | `GET /aws/estado` | Resumen de todos los recursos aprovisionados |

# | `GET /docs` | Swagger UI (interfaz interactiva para probar todo lo anterior) |

# 

# Prueba primero `GET /aws/estado` en `http://localhost:8000/docs` — si devuelve

# los datos de RDS, buckets, tabla, cola y alarmas, todo el aprovisionamiento

# funcionó.

# 

# \## 4. Verificar directamente con AWS CLI

# 

# Puedes hablarle a Floci igual que a AWS real, desde tu máquina:

# 

# ```bash

# export AWS\_ENDPOINT\_URL=http://localhost:4566

# export AWS\_ACCESS\_KEY\_ID=test

# export AWS\_SECRET\_ACCESS\_KEY=test

# export AWS\_DEFAULT\_REGION=us-east-1

# 

# aws s3 ls

# aws dynamodb list-tables

# aws sns list-topics

# aws sqs list-queues

# aws rds describe-db-instances

# aws cloudwatch describe-alarms

# ```

# 

# \## 5. Nota técnica: evaluación automática de alarmas en Floci

# 

# Floci soporta `PutMetricAlarm`, `DescribeAlarms` y `SetAlarmState`, pero \*\*no

# implementa un evaluador periódico en segundo plano\*\* como AWS real: publicar

# métricas con `PutMetricData` por encima del umbral no dispara automáticamente

# el cambio de estado de la alarma, y `SetAlarmState` cambia el estado visible

# pero no ejecuta las `AlarmActions` (no publica en SNS por sí solo).

# 

# Por eso, `POST /cloudwatch/simular-alarma` implementa a nivel de aplicación

# la publicación explícita en SNS cuando se fuerza el estado a `ALARM`,

# replicando el efecto que produciría AWS real al ejecutar la acción de la

# alarma. La configuración de las alarmas en sí (`PutMetricAlarm`, con sus

# umbrales, estadísticas y acciones) es idéntica a la que se usaría contra AWS

# real, y quedaría funcionando con evaluación automática sin ningún cambio de

# código al apuntar el mismo prototipo a CloudWatch real en vez de a Floci.

# 

# \## 6. De aquí a AWS real (ECS Fargate)

# 

# Todo el código de `aws\_setup.py` y `app.py` usa `boto3` sin nada específico de

# Floci salvo el `endpoint\_url`. Para apuntar a AWS real:

# 

# ```bash

# \# 1. Crear el repositorio en ECR

# aws ecr create-repository --repository-name infraestructura-viva-app

# 

# \# 2. Autenticar Docker contra ECR

# aws ecr get-login-password --region us-east-1 \\

# &#x20; | docker login --username AWS --password-stdin <cuenta>.dkr.ecr.us-east-1.amazonaws.com

# 

# \# 3. Etiquetar y subir la imagen de la app

# docker build -t infraestructura-viva-app .

# docker tag infraestructura-viva-app:latest \\

# &#x20; <cuenta>.dkr.ecr.us-east-1.amazonaws.com/infraestructura-viva-app:latest

# docker push <cuenta>.dkr.ecr.us-east-1.amazonaws.com/infraestructura-viva-app:latest

# 

# \# 4. En ECS: crear una Task Definition en Fargate que use esta imagen,

# \#    sin el contenedor floci (ya no hace falta), con las variables

# \#    FLOCI\_ENDPOINT vacío (o eliminada) y credenciales IAM reales en vez de

# \#    AWS\_ACCESS\_KEY\_ID=test.

# ```

# 

# En AWS real, `aws\_setup.ensure\_rds()` seguiría funcionando igual: crearía (o

# detectaría) la instancia RDS real y esperaría a que quedara `available`, tal

# como lo hace hoy contra el contenedor Postgres que levanta Floci.

# 

# \## 7. Qué valida este prototipo

# 

# \- Que las llamadas reales de `boto3` (RDS, S3, DynamoDB, SNS, SQS, CloudWatch)

# &#x20; aprovisionan correctamente los recursos de la arquitectura del documento.

# \- Que la app se conecta a RDS únicamente por los datos que devuelve la propia

# &#x20; API de AWS (`describe\_db\_instances`), no por una configuración fija — el

# &#x20; mismo patrón que se usaría contra un RDS real.

# \- Que el flujo de notificación SNS → SQS funciona de punta a punta.

# \- Que las 5 consultas SQL y la consulta NoSQL equivalente devuelven resultados

# &#x20; correctos sobre datos reales.

# \- Que las alarmas de CloudWatch quedan correctamente configuradas con sus

# &#x20; umbrales y su acción de notificación SNS.

