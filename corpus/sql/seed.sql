-- ACME GmbH — Kompass demo seed data (SQLite)
-- Idempotent: safe to re-run. Dataset "today" is 2026-07-04. All amounts in EUR.

DROP TABLE IF EXISTS refunds;
DROP TABLE IF EXISTS tickets;
DROP TABLE IF EXISTS order_items;
DROP TABLE IF EXISTS orders;
DROP TABLE IF EXISTS employees;

CREATE TABLE employees (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  email TEXT NOT NULL UNIQUE,
  department TEXT NOT NULL,
  hire_date TEXT NOT NULL,
  vacation_days_total INTEGER NOT NULL,
  vacation_days_used INTEGER NOT NULL
);

CREATE TABLE orders (
  id INTEGER PRIMARY KEY,
  customer_name TEXT NOT NULL,
  customer_email TEXT NOT NULL,
  order_date TEXT NOT NULL,
  delivery_date TEXT,
  status TEXT NOT NULL CHECK (status IN ('processing','shipped','delivered','returned','cancelled')),
  total_eur REAL NOT NULL,
  notes TEXT
);

CREATE TABLE order_items (
  order_id INTEGER NOT NULL REFERENCES orders(id),
  product TEXT NOT NULL,
  qty INTEGER NOT NULL,
  unit_price_eur REAL NOT NULL
);

CREATE TABLE tickets (
  id INTEGER PRIMARY KEY,
  customer_email TEXT NOT NULL,
  subject TEXT NOT NULL,
  body TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('open','pending','resolved')),
  priority TEXT NOT NULL CHECK (priority IN ('low','medium','high')),
  created_at TEXT NOT NULL,
  resolved_at TEXT,
  order_id INTEGER REFERENCES orders(id)
);

CREATE TABLE refunds (
  id INTEGER PRIMARY KEY,
  order_id INTEGER NOT NULL REFERENCES orders(id),
  amount_eur REAL NOT NULL,
  reason TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('requested','approved','rejected','completed')),
  requested_at TEXT NOT NULL,
  decided_at TEXT,
  approved_by TEXT
);

-- ---------------------------------------------------------------------------
-- employees (6 rows) — 28 vacation days/year full-time
-- ---------------------------------------------------------------------------
INSERT INTO employees (id, name, email, department, hire_date, vacation_days_total, vacation_days_used) VALUES
  ('emp-1001', 'Jonas Weber',   'jonas.weber@acme.de',   'Operations', '2021-03-01', 28, 11),
  ('emp-1002', 'Sabine Krüger', 'sabine.krueger@acme.de','HR',         '2019-08-15', 28, 14),
  ('emp-1003', 'Anna Schmidt',  'anna.schmidt@acme.de',  'IT',         '2022-01-10', 28, 6),
  ('emp-1004', 'Tobias Brandt', 'tobias.brandt@acme.de', 'Finance',    '2020-05-04', 28, 19),
  ('emp-1005', 'Elif Yilmaz',   'elif.yilmaz@acme.de',   'Support',    '2023-09-01', 28, 9),
  ('emp-1006', 'Marco Rossi',   'marco.rossi@acme.de',   'Support',    '2024-02-15', 28, 3);

-- ---------------------------------------------------------------------------
-- orders (12 rows) — totals equal the sum of their order_items lines.
-- Standard shipping (Germany) is free on orders over 50 EUR; smaller orders
-- carry a 'Standard Shipping' line at 4.95 EUR.
-- ---------------------------------------------------------------------------
INSERT INTO orders (id, customer_name, customer_email, order_date, delivery_date, status, total_eur, notes) VALUES
  (4460, 'Markus Vogel',   'markus.vogel@gmx.de',      '2026-05-04', '2026-05-08', 'delivered',  49.94, NULL),
  (4461, 'Julia Becker',   'julia.becker@web.de',      '2026-05-10', '2026-05-14', 'returned',  608.99, 'Returned: defective panel. Refund of 608.99 EUR completed with supervisor approval (amount over 500 EUR).'),
  (4462, 'Daniel Hoffmann','daniel.hoffmann@gmail.com','2026-05-18', '2026-05-22', 'delivered', 403.99, NULL),
  (4464, 'Nina Weiß',      'nina.weiss@web.de',        '2026-06-01', '2026-06-04', 'delivered', 129.98, NULL),
  (4465, 'Ahmed Khan',     'ahmed.khan@gmail.com',     '2026-06-05', NULL,         'cancelled', 199.00, 'Cancelled by customer before dispatch; payment authorization voided.'),
  (4466, 'Petra Schulz',   'petra.schulz@t-online.de', '2026-06-08', '2026-06-11', 'returned',  129.99, 'Returned: damaged in transit. Full refund of 129.99 EUR completed.'),
  (4467, 'Lukas Braun',    'lukas.braun@gmx.de',       '2026-06-12', NULL,         'processing',449.00, 'Awaiting stock allocation at Berlin warehouse.'),
  (4469, 'Katrin Lehmann', 'katrin.lehmann@web.de',    '2026-06-20', NULL,         'shipped',    34.94, NULL),
  (4470, 'Omar El-Sayed',  'omar.elsayed@outlook.com', '2026-06-24', '2026-06-27', 'delivered', 188.00, NULL),
  (4471, 'Lena Fischer',   'lena.fischer@web.de',      '2026-06-25', '2026-06-28', 'delivered', 189.99, 'Customer reported on 2026-07-02 that the order arrived damaged; see ticket 88012.'),
  (4473, 'Emma Wagner',    'emma.wagner@t-online.de',  '2026-06-30', NULL,         'shipped',    59.99, NULL),
  (4475, 'Felix Neumann',  'felix.neumann@gmx.de',     '2026-07-02', NULL,         'processing',155.00, NULL);

-- ---------------------------------------------------------------------------
-- order_items — line sums match orders.total_eur exactly
-- ---------------------------------------------------------------------------
-- 4460: 44.99 + 4.95 = 49.94
INSERT INTO order_items (order_id, product, qty, unit_price_eur) VALUES
  (4460, 'FlexiStand Laptop Riser', 1, 44.99),
  (4460, 'Standard Shipping', 1, 4.95);

-- 4461: 549.00 + 59.99 = 608.99
INSERT INTO order_items (order_id, product, qty, unit_price_eur) VALUES
  (4461, 'ClearView 32" 4K Monitor', 1, 549.00),
  (4461, 'LumiBar Monitor Light', 1, 59.99);

-- 4462: 379.00 + 24.99 = 403.99
INSERT INTO order_items (order_id, product, qty, unit_price_eur) VALUES
  (4462, 'ErgoDesk Standing Desk Frame', 1, 379.00),
  (4462, 'CableTidy Under-Desk Tray', 1, 24.99);

-- 4464: 89.99 + 39.99 = 129.98
INSERT INTO order_items (order_id, product, qty, unit_price_eur) VALUES
  (4464, 'TypeMaster Mechanical Keyboard', 1, 89.99),
  (4464, 'PrecisionGlide Wireless Mouse', 1, 39.99);

-- 4465: 199.00
INSERT INTO order_items (order_id, product, qty, unit_price_eur) VALUES
  (4465, 'NoiseGuard ANC Headphones', 1, 199.00);

-- 4466: 129.99
INSERT INTO order_items (order_id, product, qty, unit_price_eur) VALUES
  (4466, 'DualMount Monitor Arm', 1, 129.99);

-- 4467: 449.00
INSERT INTO order_items (order_id, product, qty, unit_price_eur) VALUES
  (4467, 'ErgoChair Pro Office Chair', 1, 449.00);

-- 4469: 29.99 + 4.95 = 34.94
INSERT INTO order_items (order_id, product, qty, unit_price_eur) VALUES
  (4469, 'DeskPad XXL Desk Mat', 1, 29.99),
  (4469, 'Standard Shipping', 1, 4.95);

-- 4470: 159.00 + 2 x 14.50 = 188.00
INSERT INTO order_items (order_id, product, qty, unit_price_eur) VALUES
  (4470, 'PowerHub 90W USB-C Docking Station', 1, 159.00),
  (4470, 'HDMI 2.1 Cable 2m', 2, 14.50);

-- 4471 (canonical demo order): 79.99 + 110.00 = 189.99
INSERT INTO order_items (order_id, product, qty, unit_price_eur) VALUES
  (4471, 'ErgoDesk Monitor Arm', 1, 79.99),
  (4471, 'AcousticPro USB Microphone', 1, 110.00);

-- 4473: 59.99
INSERT INTO order_items (order_id, product, qty, unit_price_eur) VALUES
  (4473, 'LumiBar Monitor Light', 1, 59.99);

-- 4475: 110.00 + 45.00 = 155.00
INSERT INTO order_items (order_id, product, qty, unit_price_eur) VALUES
  (4475, 'AcousticPro USB Microphone', 1, 110.00),
  (4475, 'BoomArm Pro Microphone Stand', 1, 45.00);

-- ---------------------------------------------------------------------------
-- tickets (8 rows) — includes canonical ticket 88012
-- ---------------------------------------------------------------------------
INSERT INTO tickets (id, customer_email, subject, body, status, priority, created_at, resolved_at, order_id) VALUES
  (88003, 'julia.becker@web.de', 'Dead pixels on ClearView monitor (order 4461)',
   'Hello, the ClearView 32" 4K Monitor from order 4461 shows a vertical line of dead pixels straight out of the box. This is clearly a defect. I would like to return the order and receive a full refund. Please send me a return label.',
   'resolved', 'high', '2026-05-15', '2026-05-18', 4461),
  (88005, 'petra.schulz@t-online.de', 'Monitor arm arrived with cracked clamp',
   'Good afternoon, the DualMount Monitor Arm from order 4466 arrived with a cracked mounting clamp. The outer carton was visibly dented. I am requesting a full refund including shipping as the item is damaged. Photos are attached to my original email.',
   'resolved', 'medium', '2026-06-11', '2026-06-14', 4466),
  (88007, 'ahmed.khan@gmail.com', 'Request to cancel order 4465',
   'Hi, I ordered the NoiseGuard ANC Headphones earlier today (order 4465) but I found a better option elsewhere. Please cancel the order before it ships and release the payment authorization. Thank you.',
   'resolved', 'medium', '2026-06-05', '2026-06-06', 4465),
  (88009, 'katrin.lehmann@web.de', 'Where is my order 4469?',
   'Hello, order 4469 was placed on 2026-06-20 and shows as shipped, but the tracking has not updated in several days. Standard shipping is supposed to take 3-5 business days within Germany. Could you check with the carrier and let me know the status?',
   'pending', 'low', '2026-06-28', NULL, 4469),
  (88010, 'markus.vogel@gmx.de', 'Invoice copy for order 4460',
   'Hi, could you send me a PDF copy of the invoice for order 4460? I need it for my employer''s expense report. The order was delivered on 2026-05-08. Thanks in advance.',
   'resolved', 'low', '2026-06-02', '2026-06-03', 4460),
  (88011, 'felix.neumann@gmx.de', 'Change delivery address for order 4475',
   'Hello, I placed order 4475 on 2026-07-02 and it is still processing. I will be at a different address next week - can you update the delivery address to Torstrasse 112, 10119 Berlin before the parcel is dispatched?',
   'open', 'medium', '2026-07-03', NULL, 4475),
  (88012, 'lena.fischer@web.de', 'Order 4471 arrived damaged',
   'Hello, my order 4471 was delivered on 2026-06-28. When I unpacked it today I found that the AcousticPro USB Microphone has a cracked housing and the ErgoDesk Monitor Arm box was crushed on one side. The order total was 189.99 EUR and I would like a full refund to my original payment method. I can provide photos of the damage on request. Kind regards, Lena Fischer',
   'open', 'high', '2026-07-02', NULL, 4471),
  (88013, 'sofia.lindqvist@outlook.com', 'Question about EU shipping times',
   'Hi, I live in Sweden and I am considering ordering a standing desk frame. Your website lists EU shipping at 12.95 EUR with 5-8 business days delivery - does that apply to bulky items as well, or is there a surcharge? Best regards, Sofia',
   'resolved', 'low', '2026-06-20', '2026-06-21', NULL);

-- ---------------------------------------------------------------------------
-- refunds (2 completed historical refunds; order 4471 has none yet — the live
-- demo creates it). Refunds over 500 EUR require supervisor approval.
-- ---------------------------------------------------------------------------
INSERT INTO refunds (id, order_id, amount_eur, reason, status, requested_at, decided_at, approved_by) VALUES
  (9001, 4461, 608.99, 'Defective item: vertical line of dead pixels on ClearView 32" 4K Monitor, reported on arrival. Full refund including shipping; amount over 500 EUR approved by Support supervisor.', 'completed', '2026-05-16', '2026-05-18', 'Elif Yilmaz'),
  (9002, 4466, 129.99, 'Damaged in transit: cracked mounting clamp on DualMount Monitor Arm, carton dented on delivery. Full refund including shipping.', 'completed', '2026-06-12', '2026-06-14', 'Marco Rossi');
