CREATE TABLE IF NOT EXISTS clientes (
  id INTEGER PRIMARY KEY,
  nombre TEXT NOT NULL,
  email TEXT NOT NULL,
  segmento TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS pedidos (
  id INTEGER PRIMARY KEY,
  cliente_id INTEGER NOT NULL,
  monto REAL NOT NULL,
  fecha TEXT NOT NULL,
  estado TEXT NOT NULL,
  FOREIGN KEY (cliente_id) REFERENCES clientes(id)
);

CREATE TABLE IF NOT EXISTS tickets_soporte (
  id INTEGER PRIMARY KEY,
  cliente_id INTEGER NOT NULL,
  asunto TEXT NOT NULL,
  prioridad TEXT NOT NULL,
  resuelto INTEGER NOT NULL DEFAULT 0,
  FOREIGN KEY (cliente_id) REFERENCES clientes(id)
);

INSERT INTO clientes (id, nombre, email, segmento) VALUES
  (1, 'Comercial Andina SpA', 'contacto@andina.cl', 'Corporativo'),
  (2, 'Juan Pérez', 'juan.perez@mail.com', 'Retail'),
  (3, 'Logística del Sur Ltda.', 'ventas@logsur.cl', 'Corporativo'),
  (4, 'María Torres', 'maria.torres@mail.com', 'Retail')
ON CONFLICT (id) DO NOTHING;

INSERT INTO pedidos (id, cliente_id, monto, fecha, estado) VALUES
  (1, 1, 1250000, '2026-05-02', 'Completado'),
  (2, 1, 890000, '2026-05-20', 'Completado'),
  (3, 2, 45000, '2026-06-01', 'Pendiente'),
  (4, 3, 2100000, '2026-06-10', 'Completado'),
  (5, 4, 32000, '2026-06-15', 'Cancelado')
ON CONFLICT (id) DO NOTHING;

INSERT INTO tickets_soporte (id, cliente_id, asunto, prioridad, resuelto) VALUES
  (1, 2, 'Error al procesar pago', 'Alta', 0),
  (2, 1, 'Consulta de factura', 'Media', 1),
  (3, 3, 'Caída del servicio de entrega', 'Alta', 0),
  (4, 4, 'Cambio de dirección de despacho', 'Baja', 1)
ON CONFLICT (id) DO NOTHING;