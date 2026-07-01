"""
Aprovisiona los recursos de 'Infraestructura Viva' contra Floci (emulador AWS)
usando boto3, igual que se haría contra AWS real. Se ejecuta una vez al
levantar la app (startup de FastAPI) y es idempotente: si los recursos ya
existen, los reutiliza en vez de fallar.
"""
import os
import time
import logging

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger("aws_setup")

FLOCI_ENDPOINT = os.environ.get("FLOCI_ENDPOINT", "http://floci:4566")
AWS_REGION = os.environ.get("AWS_DEFAULT_REGION", "us-east-1")
AWS_ACCESS_KEY = os.environ.get("AWS_ACCESS_KEY_ID", "test")
AWS_SECRET_KEY = os.environ.get("AWS_SECRET_ACCESS_KEY", "test")

DB_INSTANCE_ID = "infraestructura-viva-db"
DB_NAME = "acme_infraestructura_viva"
DB_MASTER_USER = os.environ.get("DB_MASTER_USER", "admin_acme")
DB_MASTER_PASSWORD = os.environ.get("DB_MASTER_PASSWORD", "changeme123")

BUCKET_ESTATICO = "infraestructura-viva-estatico"
BUCKET_BACKUPS = "infraestructura-viva-backups"
DYNAMO_TABLE = "infraestructura-viva-tickets-soporte"
SNS_TOPIC_NAME = "infraestructura-viva-alertas"
SQS_QUEUE_NAME = "infraestructura-viva-cola"

INIT_SQL_PATH = os.path.join(os.path.dirname(__file__), "init.sql")


def _client(service):
    return boto3.client(
        service,
        endpoint_url=FLOCI_ENDPOINT,
        region_name=AWS_REGION,
        aws_access_key_id=AWS_ACCESS_KEY,
        aws_secret_access_key=AWS_SECRET_KEY,
    )


def wait_for_floci(timeout=60):
    """Espera a que Floci acepte conexiones antes de aprovisionar nada."""
    import socket
    host = FLOCI_ENDPOINT.split("://")[1].split(":")[0]
    port = int(FLOCI_ENDPOINT.split(":")[-1])
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection((host, port), timeout=2):
                logger.info("Floci disponible en %s", FLOCI_ENDPOINT)
                return True
        except OSError:
            time.sleep(2)
    raise RuntimeError(f"Floci no respondió en {FLOCI_ENDPOINT} tras {timeout}s")


# ---------------------------------------------------------------- S3 -----
def ensure_buckets():
    s3 = _client("s3")
    for bucket in (BUCKET_ESTATICO, BUCKET_BACKUPS):
        try:
            s3.head_bucket(Bucket=bucket)
            logger.info("Bucket S3 '%s' ya existe", bucket)
        except ClientError:
            s3.create_bucket(Bucket=bucket)
            logger.info("Bucket S3 '%s' creado", bucket)
    return {"buckets": [BUCKET_ESTATICO, BUCKET_BACKUPS]}


# --------------------------------------------------------------- SNS/SQS -
def ensure_messaging():
    sns = _client("sns")
    sqs = _client("sqs")

    topic_arn = sns.create_topic(Name=SNS_TOPIC_NAME)["TopicArn"]

    queue_url = sqs.create_queue(QueueName=SQS_QUEUE_NAME)["QueueUrl"]
    queue_arn = sqs.get_queue_attributes(
        QueueUrl=queue_url, AttributeNames=["QueueArn"]
    )["Attributes"]["QueueArn"]

    # Suscribir la cola al tema (idempotente: AWS no duplica suscripciones
    # exactamente iguales, pero por las dudas listamos antes de suscribir).
    existing = sns.list_subscriptions_by_topic(TopicArn=topic_arn).get(
        "Subscriptions", []
    )
    already_subscribed = any(s["Endpoint"] == queue_arn for s in existing)
    if not already_subscribed:
        sns.subscribe(TopicArn=topic_arn, Protocol="sqs", Endpoint=queue_arn)
        logger.info("Cola SQS suscrita al tema SNS")

    logger.info("SNS topic=%s | SQS queue=%s", topic_arn, queue_url)
    return {"topic_arn": topic_arn, "queue_url": queue_url, "queue_arn": queue_arn}


# --------------------------------------------------------------- DynamoDB
def ensure_dynamodb():
    dynamodb = _client("dynamodb")
    try:
        dynamodb.describe_table(TableName=DYNAMO_TABLE)
        logger.info("Tabla DynamoDB '%s' ya existe", DYNAMO_TABLE)
    except ClientError:
        dynamodb.create_table(
            TableName=DYNAMO_TABLE,
            AttributeDefinitions=[{"AttributeName": "id", "AttributeType": "S"}],
            KeySchema=[{"AttributeName": "id", "KeyType": "HASH"}],
            BillingMode="PAY_PER_REQUEST",
        )
        dynamodb.get_waiter("table_exists").wait(TableName=DYNAMO_TABLE)
        logger.info("Tabla DynamoDB '%s' creada", DYNAMO_TABLE)

    resource = boto3.resource(
        "dynamodb",
        endpoint_url=FLOCI_ENDPOINT,
        region_name=AWS_REGION,
        aws_access_key_id=AWS_ACCESS_KEY,
        aws_secret_access_key=AWS_SECRET_KEY,
    )
    table = resource.Table(DYNAMO_TABLE)

    seed = [
        {"id": "1", "cliente": "Juan Pérez", "asunto": "Error al procesar pago",
         "prioridad": "Alta", "resuelto": False},
        {"id": "2", "cliente": "Comercial Andina SpA", "asunto": "Consulta de factura",
         "prioridad": "Media", "resuelto": True},
        {"id": "3", "cliente": "Logística del Sur Ltda.", "asunto": "Caída del servicio de entrega",
         "prioridad": "Alta", "resuelto": False},
        {"id": "4", "cliente": "María Torres", "asunto": "Cambio de dirección de despacho",
         "prioridad": "Baja", "resuelto": True},
    ]
    for item in seed:
        table.put_item(Item=item)

    return {"table": DYNAMO_TABLE}


# ------------------------------------------------------------------ RDS --
def ensure_rds():
    """
    Crea (si no existe) la instancia RDS PostgreSQL en Floci y devuelve
    los datos de conexión. Floci pasa RDS por un proxy propio con
    autenticación IAM (token temporal), distinto del modelo usuario/
    contraseña plano de un Postgres normal, así que probamos primero con
    token IAM y, si falla, caemos a la contraseña maestra.
    """
    rds = _client("rds")

    try:
        desc = rds.describe_db_instances(DBInstanceIdentifier=DB_INSTANCE_ID)
        instances = desc.get("DBInstances", [])
    except ClientError:
        instances = []

    if not instances:
        rds.create_db_instance(
            DBInstanceIdentifier=DB_INSTANCE_ID,
            DBInstanceClass="db.t3.micro",
            Engine="postgres",
            MasterUsername=DB_MASTER_USER,
            MasterUserPassword=DB_MASTER_PASSWORD,
            AllocatedStorage=20,
            DBName=DB_NAME,
        )
        logger.info("Instancia RDS '%s' solicitada, esperando disponibilidad...",
                     DB_INSTANCE_ID)
    else:
        logger.info("Instancia RDS '%s' ya existe", DB_INSTANCE_ID)

    # Esperar a que quede "available" (Floci levanta el contenedor real de Postgres)
    deadline = time.time() + 120
    endpoint = None
    while time.time() < deadline:
        desc = rds.describe_db_instances(DBInstanceIdentifier=DB_INSTANCE_ID)
        found = desc.get("DBInstances", [])
        if found:
            instance = found[0]
            status = instance["DBInstanceStatus"]
            if status == "available":
                endpoint = instance["Endpoint"]
                break
            logger.info("RDS status=%s, esperando...", status)
        else:
            logger.info("RDS aún no aparece en describe_db_instances, esperando...")
        time.sleep(3)

    if endpoint is None:
        raise RuntimeError("La instancia RDS no quedó disponible a tiempo")

    host, port = endpoint["Address"], endpoint["Port"]
    logger.info("RDS disponible en %s:%s", host, port)

    return {
        "host": host,
        "port": port,
        "dbname": DB_NAME,
        "user": DB_MASTER_USER,
        "password": DB_MASTER_PASSWORD,
        "rds_client_for_iam": rds,
    }


def get_rds_connection(rds_info):
    """
    Devuelve una conexión psycopg2 a la instancia RDS emulada.
    Intenta primero autenticación IAM (token generado con la API de RDS);
    si Floci no la exige o falla, usa la contraseña maestra directamente.
    """
    import psycopg2

    host, port, dbname, user = (
        rds_info["host"], rds_info["port"], rds_info["dbname"], rds_info["user"]
    )

    try:
        token = rds_info["rds_client_for_iam"].generate_db_auth_token(
            DBHostname=host, Port=port, DBUsername=user, Region=AWS_REGION
        )
        return psycopg2.connect(
            host=host, port=port, dbname=dbname, user=user, password=token,
            connect_timeout=5,
        )
    except Exception as exc:  # noqa: BLE001 - fallback intencional
        logger.warning("Autenticación IAM a RDS falló (%s), probando contraseña maestra", exc)
        return psycopg2.connect(
            host=host, port=port, dbname=dbname, user=user,
            password=rds_info["password"], connect_timeout=5,
        )


def apply_schema(rds_info):
    """Aplica init.sql sobre la instancia RDS. Idempotente: ignora errores
    de 'ya existe' para poder reiniciar el contenedor sin duplicar datos."""
    with open(INIT_SQL_PATH, "r", encoding="utf-8") as f:
        schema_sql = f.read()

    conn = get_rds_connection(rds_info)
    conn.autocommit = False
    try:
        with conn.cursor() as cur:
            cur.execute(schema_sql)
        conn.commit()
        logger.info("Esquema aplicado en RDS correctamente")
    except Exception as exc:  # noqa: BLE001
        conn.rollback()
        logger.warning("El esquema no se aplicó limpio (probablemente ya existía): %s", exc)
    finally:
        conn.close()


# ------------------------------------------------------------ CloudWatch
CPU_NAMESPACE = "InfraestructuraViva/Computo"
CPU_METRIC = "CPUUtilization"
CPU_DIMENSIONS = [{"Name": "InstanceId", "Value": "app-instance"}]

APP_NAMESPACE = "InfraestructuraViva/Aplicacion"
ERROR_METRIC = "ErrorCount"
ERROR_DIMENSIONS = [{"Name": "Service", "Value": "api"}]

NET_NAMESPACE = "InfraestructuraViva/Computo"
NET_METRIC = "NetworkIn"
NET_DIMENSIONS = [{"Name": "InstanceId", "Value": "app-instance"}]

ALARM_CPU = "InfraestructuraViva-CPUAlta"
ALARM_ERRORS = "InfraestructuraViva-ErroresApp"


def ensure_alarms(topic_arn):
    """
    Crea (o actualiza, PutMetricAlarm es idempotente por nombre) las dos
    alarmas de CloudWatch del plan de monitoreo, ambas conectadas al tema
    SNS ya aprovisionado. Corresponde a la Lección 8 del módulo.
    """
    cw = _client("cloudwatch")

    cw.put_metric_alarm(
        AlarmName=ALARM_CPU,
        AlarmDescription="CPU de la instancia de cómputo sobre el 70% durante 1 minuto",
        Namespace=CPU_NAMESPACE,
        MetricName=CPU_METRIC,
        Dimensions=CPU_DIMENSIONS,
        Statistic="Average",
        Period=60,
        EvaluationPeriods=1,
        Threshold=70,
        ComparisonOperator="GreaterThanThreshold",
        AlarmActions=[topic_arn],
        TreatMissingData="notBreaching",
    )
    logger.info("Alarma '%s' configurada (umbral CPU > 70%%)", ALARM_CPU)

    cw.put_metric_alarm(
        AlarmName=ALARM_ERRORS,
        AlarmDescription="Más de 5 errores de aplicación en 1 minuto",
        Namespace=APP_NAMESPACE,
        MetricName=ERROR_METRIC,
        Dimensions=ERROR_DIMENSIONS,
        Statistic="Sum",
        Period=60,
        EvaluationPeriods=1,
        Threshold=5,
        ComparisonOperator="GreaterThanThreshold",
        AlarmActions=[topic_arn],
        TreatMissingData="notBreaching",
    )
    logger.info("Alarma '%s' configurada (umbral errores > 5)", ALARM_ERRORS)

    return {"alarms": [ALARM_CPU, ALARM_ERRORS]}


def put_cpu_metric(value):
    cw = _client("cloudwatch")
    cw.put_metric_data(
        Namespace=CPU_NAMESPACE,
        MetricData=[{
            "MetricName": CPU_METRIC,
            "Dimensions": CPU_DIMENSIONS,
            "Value": value,
            "Unit": "Percent",
        }],
    )


def put_error_metric(count):
    cw = _client("cloudwatch")
    cw.put_metric_data(
        Namespace=APP_NAMESPACE,
        MetricData=[{
            "MetricName": ERROR_METRIC,
            "Dimensions": ERROR_DIMENSIONS,
            "Value": count,
            "Unit": "Count",
        }],
    )


def put_network_metric(bytes_in):
    """Tercera métrica del plan (CPU, red, errores) pedida por la Lección 8."""
    cw = _client("cloudwatch")
    cw.put_metric_data(
        Namespace=NET_NAMESPACE,
        MetricData=[{
            "MetricName": NET_METRIC,
            "Dimensions": NET_DIMENSIONS,
            "Value": bytes_in,
            "Unit": "Bytes",
        }],
    )


def describe_alarms():
    cw = _client("cloudwatch")
    resp = cw.describe_alarms(AlarmNames=[ALARM_CPU, ALARM_ERRORS])
    return [
        {"nombre": a["AlarmName"], "estado": a["StateValue"], "razon": a.get("StateReason")}
        for a in resp.get("MetricAlarms", [])
    ]


def set_alarm_state(alarm_name, state, reason):
    """
    Fuerza el estado de una alarma con SetAlarmState. Floci no incluye un
    evaluador periódico en segundo plano como AWS real (PutMetricData no
    dispara la evaluación automática), así que esta es la forma soportada
    de disparar las AlarmActions (la notificación SNS) de forma determinista
    en el emulador.
    """
    cw = _client("cloudwatch")
    cw.set_alarm_state(
        AlarmName=alarm_name,
        StateValue=state,
        StateReason=reason,
    )
    logger.info("Alarma '%s' forzada a estado %s", alarm_name, state)


# ------------------------------------------------------------ Orquestador
def provision_all():
    wait_for_floci()
    resources = {}
    resources["s3"] = ensure_buckets()
    resources["messaging"] = ensure_messaging()
    resources["dynamodb"] = ensure_dynamodb()
    resources["rds"] = ensure_rds()
    apply_schema(resources["rds"])
    resources["alarms"] = ensure_alarms(resources["messaging"]["topic_arn"])
    return resources