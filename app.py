import logging

from fastapi import FastAPI, HTTPException
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.responses import HTMLResponse

import aws_setup

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("app")

app = FastAPI(title="Infraestructura Viva - Prototipo (Floci / AWS)", docs_url=None)

# Se llena en el evento de startup con lo que devuelve aws_setup.provision_all()
AWS_RESOURCES = {}

FOOTER_HTML = """
<div style="
    position: fixed; bottom: 0; left: 0; right: 0;
    background: #1F3864; color: #D5E8F0;
    text-align: center; padding: 8px 0;
    font-family: Arial, sans-serif; font-size: 13px;
    z-index: 1000;">
    Infraestructura Viva &middot; Creado por MGLA &middot; 2026
</div>
<div style="height: 40px;"></div>
"""


@app.get("/docs", include_in_schema=False)
async def custom_docs():
    html = get_swagger_ui_html(
        openapi_url=app.openapi_url,
        title=f"{app.title} - Docs",
    ).body.decode("utf-8")
    html = html.replace("</body>", FOOTER_HTML + "</body>")
    return HTMLResponse(html)


@app.on_event("startup")
def startup():
    logger.info("Aprovisionando recursos AWS contra Floci...")
    AWS_RESOURCES.update(aws_setup.provision_all())
    logger.info("Recursos AWS listos: %s", list(AWS_RESOURCES.keys()))


def run_sql(sql: str):
    if "rds" not in AWS_RESOURCES:
        raise HTTPException(503, "RDS aún no está listo, intenta de nuevo en unos segundos")
    conn = aws_setup.get_rds_connection(AWS_RESOURCES["rds"])
    try:
        with conn.cursor() as cur:
            cur.execute(sql)
            columns = [c.name for c in cur.description]
            return [dict(zip(columns, row)) for row in cur.fetchall()]
    finally:
        conn.close()


@app.get("/")
def health():
    return {
        "status": "ok",
        "servicio": "Infraestructura Viva - Prototipo",
        "recursos_listos": list(AWS_RESOURCES.keys()),
    }


@app.get("/aws/estado")
def aws_estado():
    """Muestra qué recursos AWS reales (vía Floci) están aprovisionados."""
    if not AWS_RESOURCES:
        raise HTTPException(503, "Aún aprovisionando recursos AWS")
    rds = AWS_RESOURCES["rds"]
    return {
        "rds": {"host": rds["host"], "port": rds["port"], "dbname": rds["dbname"]},
        "s3_buckets": AWS_RESOURCES["s3"]["buckets"],
        "dynamodb_table": AWS_RESOURCES["dynamodb"]["table"],
        "sns_topic_arn": AWS_RESOURCES["messaging"]["topic_arn"],
        "sqs_queue_url": AWS_RESOURCES["messaging"]["queue_url"],
        "cloudwatch_alarms": AWS_RESOURCES.get("alarms", {}).get("alarms", []),
    }


# ------------------------------------------------------------- RDS (SQL) -
@app.get("/clientes/corporativo")
def clientes_corporativo():
    """Consulta 1: clientes por segmento (RDS)."""
    return run_sql("SELECT * FROM clientes WHERE segmento = 'Corporativo';")


@app.get("/pedidos/por-cliente")
def pedidos_por_cliente():
    """Consulta 2: pedidos por cliente (JOIN + conteo) (RDS)."""
    sql = """
        SELECT c.nombre, COUNT(p.id) AS total_pedidos
        FROM clientes c
        JOIN pedidos p ON p.cliente_id = c.id
        GROUP BY c.nombre;
    """
    return run_sql(sql)


@app.get("/pedidos/ticket-promedio")
def ticket_promedio():
    """Consulta 3: ticket promedio de pedidos completados (RDS)."""
    return run_sql("SELECT AVG(monto) AS ticket_promedio FROM pedidos WHERE estado = 'Completado';")


@app.get("/pedidos/por-estado")
def pedidos_por_estado():
    """Consulta 5: distribución de pedidos por estado (RDS)."""
    sql = """
        SELECT estado, COUNT(*) AS cantidad
        FROM pedidos
        GROUP BY estado
        ORDER BY cantidad DESC;
    """
    return run_sql(sql)


# --------------------------------------------------------------- DynamoDB
@app.get("/dynamodb/tickets")
def dynamodb_tickets():
    """Consulta 4 equivalente, pero servida desde DynamoDB (NoSQL real vía Floci)."""
    import boto3
    resource = boto3.resource(
        "dynamodb",
        endpoint_url=aws_setup.FLOCI_ENDPOINT,
        region_name=aws_setup.AWS_REGION,
        aws_access_key_id=aws_setup.AWS_ACCESS_KEY,
        aws_secret_access_key=aws_setup.AWS_SECRET_KEY,
    )
    table = resource.Table(AWS_RESOURCES["dynamodb"]["table"])
    items = table.scan()["Items"]
    return [i for i in items if i.get("prioridad") == "Alta" and not i.get("resuelto")]


# ---------------------------------------------------------------------S3-
@app.get("/s3/buckets")
def s3_buckets():
    import boto3
    s3 = boto3.client(
        "s3",
        endpoint_url=aws_setup.FLOCI_ENDPOINT,
        region_name=aws_setup.AWS_REGION,
        aws_access_key_id=aws_setup.AWS_ACCESS_KEY,
        aws_secret_access_key=aws_setup.AWS_SECRET_KEY,
    )
    return [b["Name"] for b in s3.list_buckets()["Buckets"]]


# ----------------------------------------------------------------SNS/SQS-
@app.post("/sns/notificar")
def sns_notificar(asunto: str, mensaje: str):
    """Simula una alarma de CloudWatch publicando una alerta en el tema SNS."""
    import boto3
    sns = boto3.client(
        "sns",
        endpoint_url=aws_setup.FLOCI_ENDPOINT,
        region_name=aws_setup.AWS_REGION,
        aws_access_key_id=aws_setup.AWS_ACCESS_KEY,
        aws_secret_access_key=aws_setup.AWS_SECRET_KEY,
    )
    resp = sns.publish(
        TopicArn=AWS_RESOURCES["messaging"]["topic_arn"], Subject=asunto, Message=mensaje
    )
    return {"message_id": resp["MessageId"]}


@app.get("/sqs/mensajes")
def sqs_mensajes():
    """Lee los mensajes que llegaron a la cola SQS suscrita al tema SNS."""
    import boto3
    sqs = boto3.client(
        "sqs",
        endpoint_url=aws_setup.FLOCI_ENDPOINT,
        region_name=aws_setup.AWS_REGION,
        aws_access_key_id=aws_setup.AWS_ACCESS_KEY,
        aws_secret_access_key=aws_setup.AWS_SECRET_KEY,
    )
    resp = sqs.receive_message(
        QueueUrl=AWS_RESOURCES["messaging"]["queue_url"], MaxNumberOfMessages=10, WaitTimeSeconds=1
    )
    messages = resp.get("Messages", [])
    # Borramos lo leído para no volver a verlo en la próxima consulta
    for m in messages:
        sqs.delete_message(
            QueueUrl=AWS_RESOURCES["messaging"]["queue_url"],
            ReceiptHandle=m["ReceiptHandle"],
        )
    return messages


@app.post("/sqs/purgar")
def sqs_purgar():
    """Vacía la cola por completo (útil para pruebas limpias)."""
    import boto3
    sqs = boto3.client(
        "sqs",
        endpoint_url=aws_setup.FLOCI_ENDPOINT,
        region_name=aws_setup.AWS_REGION,
        aws_access_key_id=aws_setup.AWS_ACCESS_KEY,
        aws_secret_access_key=aws_setup.AWS_SECRET_KEY,
    )
    sqs.purge_queue(QueueUrl=AWS_RESOURCES["messaging"]["queue_url"])
    return {"status": "cola vaciada"}


# ------------------------------------------------------------ CloudWatch
@app.get("/cloudwatch/alarmas")
def cloudwatch_alarmas():
    """Estado actual de las 2 alarmas del plan de monitoreo."""
    return aws_setup.describe_alarms()


@app.post("/cloudwatch/metrica-cpu")
def cloudwatch_metrica_cpu(valor: float):
    """
    Publica un dato de la métrica CPUUtilization. Con un valor > 70 deberías
    ver, tras unos segundos, que la alarma InfraestructuraViva-CPUAlta pasa
    a estado ALARM en GET /cloudwatch/alarmas.
    """
    aws_setup.put_cpu_metric(valor)
    return {"metrica": "CPUUtilization", "valor_publicado": valor}


@app.post("/cloudwatch/metrica-errores")
def cloudwatch_metrica_errores(cantidad: float):
    """
    Publica un dato de la métrica ErrorCount. Con un valor > 5 deberías ver
    la alarma InfraestructuraViva-ErroresApp pasar a estado ALARM.
    """
    aws_setup.put_error_metric(cantidad)
    return {"metrica": "ErrorCount", "valor_publicado": cantidad}


@app.post("/cloudwatch/metrica-red")
def cloudwatch_metrica_red(bytes_in: float):
    """
    Publica un dato de la métrica NetworkIn (tercera métrica clave del
    plan de monitoreo, junto a CPU y errores, según pide la Lección 8).
    """
    aws_setup.put_network_metric(bytes_in)
    return {"metrica": "NetworkIn", "valor_publicado": bytes_in}


@app.post("/cloudwatch/simular-alarma")
def cloudwatch_simular_alarma(alarma: str, estado: str = "ALARM"):
    """
    Fuerza el estado de una alarma con SetAlarmState. Floci cambia el
    estado pero, a diferencia de AWS real, no ejecuta las AlarmActions
    configuradas (no publica en SNS por sí solo). Por eso, cuando el
    nuevo estado es ALARM, este endpoint también publica la notificación
    en el tema SNS directamente, replicando el comportamiento que tendría
    AWS al disparar la acción de la alarma.
    """
    razon = f"Simulación manual del prototipo: umbral superado ({estado})"
    aws_setup.set_alarm_state(alarma, estado, razon)

    resultado = {"alarma": alarma, "nuevo_estado": estado, "notificacion_sns": None}

    if estado == "ALARM":
        import boto3
        sns = boto3.client(
            "sns",
            endpoint_url=aws_setup.FLOCI_ENDPOINT,
            region_name=aws_setup.AWS_REGION,
            aws_access_key_id=aws_setup.AWS_ACCESS_KEY,
            aws_secret_access_key=aws_setup.AWS_SECRET_KEY,
        )
        resp = sns.publish(
            TopicArn=AWS_RESOURCES["messaging"]["topic_arn"],
            Subject=f"ALARMA: {alarma}",
            Message=razon,
        )
        resultado["notificacion_sns"] = resp["MessageId"]

    return resultado