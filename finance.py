from flask import Flask, request, g, redirect, url_for, render_template_string
import sqlite3
from datetime import datetime, date
from collections import defaultdict
import calendar

DB_PATH = 'finance.db'
app = Flask(__name__)

# -------------------- База данных --------------------
def get_db():
    db = getattr(g, '_database', None)
    if db is None:
        db = g._database = sqlite3.connect(DB_PATH)
        db.row_factory = sqlite3.Row
    return db

def init_db():
    db = get_db()
    db.execute('''
    CREATE TABLE IF NOT EXISTS transactions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        type TEXT NOT NULL,
        category TEXT NOT NULL,
        amount REAL NOT NULL,
        date TEXT NOT NULL,
        comment TEXT
    );
    ''')
    db.execute('CREATE INDEX IF NOT EXISTS idx_transactions_date ON transactions(date);')
    db.execute('CREATE INDEX IF NOT EXISTS idx_transactions_type ON transactions(type);')
    db.commit()

@app.teardown_appcontext
def close_connection(exception):
    db = getattr(g, '_database', None)
    if db is not None:
        db.close()

# -------------------- База данных операции --------------------
def insert_transaction(tx_type, category, amount, dt_iso, comment):
    db = get_db()
    db.execute(
        'INSERT INTO transactions (type, category, amount, date, comment) VALUES (?, ?, ?, ?, ?)',
        (tx_type, category, amount, dt_iso, comment)
    )
    db.commit()

def fetch_transactions(period_from=None, period_to=None, category_query=None):
    db = get_db()
    q = 'SELECT * FROM transactions WHERE 1=1'
    params = []

    if period_from:
        q += ' AND date >= ?'
        params.append(period_from)

    if period_to:
        q += ' AND date <= ?'
        params.append(period_to)

    q += ' ORDER BY date DESC'
    cur = db.execute(q, params)
    rows = [dict(row) for row in cur.fetchall()]

    # фильтр по категории в Python (работает с русским и любыми буквами)
    if category_query:
        needle = category_query.strip().casefold()
        rows = [
            r for r in rows
            if needle in str(r.get('category', '')).strip().casefold()
        ]

    return rows

def get_transaction_by_id(tx_id):
    db = get_db()
    cur = db.execute('SELECT * FROM transactions WHERE id = ?', (tx_id,))
    row = cur.fetchone()
    return dict(row) if row else None

def update_transaction(tx_id, tx_type, category, amount, dt_iso, comment):
    db = get_db()
    db.execute('''
        UPDATE transactions
        SET type=?, category=?, amount=?, date=?, comment=?
        WHERE id=?
    ''', (tx_type, category, amount, dt_iso, comment, tx_id))
    db.commit()

def delete_transaction(tx_id):
    db = get_db()
    db.execute('DELETE FROM transactions WHERE id = ?', (tx_id,))
    db.commit()

# -------------------- Индексы --------------------
@app.route('/')
def index():
    init_db()
    frm = request.args.get('from')
    to = request.args.get('to')
    q = request.args.get('q', '').strip()
    month = request.args.get('month', '')  # формат YYYY-MM

    # операции по фильтрам
    transactions = fetch_transactions(frm, to, q if q else None)

    # итоги + суммы расходов по категориям (по отфильтрованным операциям)
    totals = {'income': 0.0, 'expense': 0.0}
    expense_category_sums = defaultdict(float)

    for t in transactions:
        amt = float(t['amount'])
        if t['type'] == 'income':
            totals['income'] += amt
        elif t['type'] == 'expense':
            totals['expense'] += amt
            expense_category_sums[t['category']] += amt

    expense_categories = list(expense_category_sums.keys())
    expense_cat_values = [round(expense_category_sums[c], 2) for c in expense_categories]

    # доходы/расходы по дням месяца (для выбранного месяца или текущего)
    today = date.today()
    if month:
        try:
            year, mon = map(int, month.split('-'))
        except Exception:
            year, mon = today.year, today.month
    else:
        year, mon = today.year, today.month

    days_in_month = calendar.monthrange(year, mon)[1]
    income_by_day = [0.0] * days_in_month
    expense_by_day = [0.0] * days_in_month

    first_day = date(year, mon, 1).isoformat()
    last_day = date(year, mon, days_in_month).isoformat()

    monthly_transactions = fetch_transactions(first_day, last_day, None)
    for tx in monthly_transactions:
        try:
            d = datetime.fromisoformat(tx['date']).day
        except Exception:
            continue
        amt = float(tx['amount'])
        if tx['type'] == 'income':
            income_by_day[d - 1] += amt
        elif tx['type'] == 'expense':
            expense_by_day[d - 1] += amt

    income_labels = list(range(1, days_in_month + 1))
    income_values = [round(v, 2) for v in income_by_day]
    expense_values = [round(v, 2) for v in expense_by_day]

    if month:
        display_month = f"{year}-{mon:02d}"
    else:
        display_month = today.strftime('%Y-%m')

    return render_template_string(
        TEMPLATE,
        transactions=transactions,
        totals=totals,
        request=request,
        today=today.isoformat(),
        income_labels=income_labels,
        income_values=income_values,
        expense_values=expense_values,
        display_month=display_month,
        default_month=f"{year}-{mon:02d}",
        expense_categories=expense_categories,
        expense_cat_values=expense_cat_values,
    )

@app.route('/add', methods=['POST'])
def add():
    init_db()
    tx_type = request.form['type']
    category = request.form['category']
    try:
        amount = float(request.form['amount'])
    except Exception:
        amount = 0.0
    dt_iso = request.form['date']
    comment = request.form.get('comment')
    if amount <= 0:
        return redirect(url_for('index'))
    insert_transaction(tx_type, category, amount, dt_iso, comment)
    return redirect(url_for('index'))

@app.route('/delete/<int:tx_id>')
def delete(tx_id):
    delete_transaction(tx_id)
    return redirect(url_for('index'))

@app.route('/edit/<int:tx_id>', methods=['GET', 'POST'])
def edit(tx_id):
    init_db()
    tx = get_transaction_by_id(tx_id)
    if not tx:
        return "Transaction not found", 404

    if request.method == 'POST':
        tx_type = request.form['type']
        category = request.form['category']
        try:
            amount = float(request.form['amount'])
        except Exception:
            amount = 0.0
        dt_iso = request.form['date']
        comment = request.form.get('comment')
        if amount <= 0:
            return redirect(url_for('index'))
        update_transaction(tx_id, tx_type, category, amount, dt_iso, comment)
        return redirect(url_for('index'))

    edit_form = f"""
    <!doctype html>
    <html lang="ru">
    <head>
      <meta charset="utf-8">
      <title>Редактирование: {tx['category']}</title>
      <link rel="stylesheet"
            href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css">
    </head>
    <body class="bg-light">
      <div class="container py-5">
        <div class="row justify-content-center">
          <div class="col-md-8 col-lg-6">
            <div class="card shadow-sm">
              <div class="card-header bg-white border-0 pb-0">
                <h3 class="mb-1">Редактирование: {tx['category']}</h3>
                <p class="text-muted small mb-0">
                  Измени данные операции и нажми «Сохранить»
                </p>
              </div>
              <div class="card-body">
                <form method="post">
                  <div class="mb-3">
                    <label class="form-label">Тип операции</label>
                    <select name="type" class="form-select">
                      <option value="income" {"selected" if tx['type'] == 'income' else ""}>Доход</option>
                      <option value="expense" {"selected" if tx['type'] == 'expense' else ""}>Расход</option>
                    </select>
                  </div>

                  <div class="mb-3">
                    <label class="form-label">Категория</label>
                    <input name="category" class="form-control" value="{tx['category']}">
                  </div>

                  <div class="mb-3">
                    <label class="form-label">Сумма</label>
                    <input name="amount" type="number" step="0.01"
                           class="form-control" value="{tx['amount']}">
                  </div>

                  <div class="mb-3">
                    <label class="form-label">Дата</label>
                    <input name="date" type="date" class="form-control"
                           value="{tx['date']}">
                  </div>

                  <div class="mb-3">
                    <label class="form-label">Комментарий</label>
                    <input name="comment" class="form-control"
                           value="{tx['comment'] or ''}">
                  </div>

                  <div class="d-flex justify-content-end gap-2 mt-4">
                    <a href="/" class="btn btn-outline-secondary">Отмена</a>
                    <button class="btn btn-success" type="submit">Сохранить</button>
                  </div>
                </form>
              </div>
            </div>
          </div>
        </div>
      </div>
    </body>
    </html>
    """

    return edit_form

# -------------------- HTML Template --------------------
TEMPLATE = """
<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <title>Финансы</title>
  <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css"
        rel="stylesheet">
  <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
  <style>
    body {
      padding-top: 70px; /* чтобы контент не залез под фиксированную панель */
    }
    .summary-bar {
      position: fixed;
      top: 0;
      left: 0;
      right: 0;
      z-index: 1030;
      background: #ffffff;
      box-shadow: 0 2px 4px rgba(0,0,0,.1);
    }
    .summary-bar .value-positive {
      color: #198754; /* bootstrap success */
      font-weight: 600;
    }
    .summary-bar .value-negative {
      color: #dc3545; /* bootstrap danger */
      font-weight: 600;
    }
  </style>
</head>
<body class="bg-light">

<!-- ФИКСИРОВАННАЯ ПАНЕЛЬ ИТОГО -->
<div class="summary-bar">
  <div class="container py-2">
    <div class="d-flex flex-wrap justify-content-between align-items-center gap-2">
      <div>
        <strong>Баланс:</strong>
        {% set balance = totals['income'] - totals['expense'] %}
        <span class="{{ 'value-positive' if balance >= 0 else 'value-negative' }}">
          {{ '%.2f'|format(balance) }}
        </span>
      </div>
      <div>
        <strong>Доход:</strong>
        <span class="value-positive">
          {{ '%.2f'|format(totals['income']) }}
        </span>
      </div>
      <div>
        <strong>Расход:</strong>
        <span class="value-negative">
          {{ '%.2f'|format(totals['expense']) }}
        </span>
      </div>
    </div>
  </div>
</div>

<div class="container py-4">
  <h1 class="mb-4">Учет финансов</h1>

  <div class="row">
    <div class="col-md-4">
      <div class="card mb-3">
        <div class="card-header">Добавить операцию</div>
        <div class="card-body">
          <form method="post" action="{{ url_for('add') }}">
            <div class="mb-3">
              <label class="form-label">Тип</label>
              <select name="type" class="form-select">
                <option value="income">Доход</option>
                <option value="expense">Расход</option>
              </select>
            </div>
            <div class="mb-3">
              <label class="form-label">Категория</label>
              <input name="category" class="form-control" required>
            </div>
            <div class="mb-3">
              <label class="form-label">Сумма</label>
              <input name="amount" type="number" step="0.01" class="form-control" required>
            </div>
            <div class="mb-3">
              <label class="form-label">Дата</label>
              <input name="date" type="date" class="form-control" value="{{ today }}" required>
            </div>
            <div class="mb-3">
              <label class="form-label">Комментарий</label>
              <input name="comment" class="form-control">
            </div>
            <button class="btn btn-primary">Добавить</button>
          </form>
        </div>
      </div>

      <div class="card mb-3">
        <div class="card-header">Фильтр</div>
        <div class="card-body">
          <form method="get" action="{{ url_for('index') }}">
            <div class="mb-3">
              <label class="form-label">С даты</label>
              <input name="from" type="date" class="form-control" value="{{ request.args.get('from','') }}">
            </div>
            <div class="mb-3">
              <label class="form-label">По дату</label>
              <input name="to" type="date" class="form-control" value="{{ request.args.get('to','') }}">
            </div>
            <div class="mb-3">
              <label class="form-label">Категория (поиск)</label>
              <input name="q" class="form-control" value="{{ request.args.get('q','') }}" placeholder="Часть названия категории">
            </div>
            <div class="mb-3">
              <label class="form-label">Месяц для графика</label>
              <input name="month" type="month" class="form-control" value="{{ request.args.get('month', default_month) }}">
            </div>
            <button class="btn btn-secondary">Применить</button>
            <a href="{{ url_for('index') }}" class="btn btn-link">Сбросить</a>
          </form>
        </div>
      </div>

    </div>

    <div class="col-md-8">
      <div class="card mb-3">
        <div class="card-header">Операции</div>
        <div class="card-body p-0">
          <table class="table table-striped mb-0">
            <thead>
              <tr>
                <th>Дата</th>
                <th>Тип</th>
                <th>Категория</th>
                <th>Сумма</th>
                <th>Комментарий</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
            {% for t in transactions %}
              <tr class="{% if t['type']=='income' %}table-success{% else %}table-danger{% endif %}">
                <td>{{ t['date'] }}</td>
                <td>{{ 'Доход' if t['type']=='income' else 'Расход' }}</td>
                <td>{{ t['category'] }}</td>
                <td>{{ '%.2f'|format(t['amount']) }}</td>
                <td>{{ t['comment'] or '' }}</td>
                <td class="text-end">
                  <a href="{{ url_for('edit', tx_id=t['id']) }}" class="btn btn-sm btn-outline-primary">✎</a>
                  <a href="{{ url_for('delete', tx_id=t['id']) }}" class="btn btn-sm btn-outline-danger"
                     onclick="return confirm('Удалить операцию?');">✕</a>
                </td>
              </tr>
            {% endfor %}
            </tbody>
          </table>
        </div>
      </div>

      <div class="row">
        <div class="col-md-6 mb-3">
          <div class="card h-100">
            <div class="card-header">Расходы по категориям (отфильтрованные)</div>
            <div class="card-body">
              <canvas id="expenseCatChart"></canvas>
            </div>
          </div>
        </div>
        <div class="col-md-6 mb-3">
          <div class="card h-100">
            <div class="card-header">Доходы и расходы по дням ({{ display_month }})</div>
            <div class="card-body">
              <canvas id="incomeExpenseChart"></canvas>
            </div>
          </div>
        </div>
      </div>

    </div>
  </div>
</div>

<script>
     // график расходов по категориям (круговая)
    const catCtx = document.getElementById('expenseCatChart').getContext('2d');
    const expenseCatChart = new Chart(catCtx, {
     type: 'pie',
    data: {
     labels: {{ expense_categories|tojson }},
     datasets: [{
        data: {{ expense_cat_values|tojson }},
        backgroundColor: [
            '#ff6384', '#36a2eb', '#ffcd56',
            '#4bc0c0', '#9966ff', '#ff9f40',
            '#66d18f', '#d16666', '#6a6ad1'
        ]
        }]
    },
  options: {
    responsive: true,
    plugins: {
      legend: {
        position: 'bottom'
      }
    }
  }
});


  // график доходов и расходов по дням
  const ctx = document.getElementById('incomeExpenseChart').getContext('2d');
  const incomeExpenseChart = new Chart(ctx, {
    type: 'line',
    data: {
      labels: {{ income_labels|tojson }},
      datasets: [
        {
          label: 'Доход',
          data: {{ income_values|tojson }},
          tension: 0.3
        },
        {
          label: 'Расход',
          data: {{ expense_values|tojson }},
          tension: 0.3
        }
      ]
    },
    options: {
      responsive: true
    }
  });
</script>

</body>
</html>
"""

if __name__ == '__main__':
    with app.app_context():
        init_db()
    app.run(debug=True)