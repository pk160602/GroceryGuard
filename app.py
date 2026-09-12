import os
import sqlite3
from datetime import date, timedelta

import psycopg2
from psycopg2.extras import RealDictCursor
from flask import Flask, render_template, request, redirect, url_for, flash

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "groceryguard-dev-key")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SQLITE_DATABASE = os.path.join(BASE_DIR, "grocery_shop.db")
DATABASE_URL = os.environ.get("DATABASE_URL")


class DBConnection:
    """Small compatibility wrapper so the same app works with SQLite locally
    and PostgreSQL on Render."""
    def __init__(self, connection, is_postgres=False):
        self.connection = connection
        self.is_postgres = is_postgres

    def execute(self, query, params=()):
        if self.is_postgres:
            query = query.replace("?", "%s")
            return self.connection.cursor().execute(query, params)
        return self.connection.execute(query, params)

    def executemany(self, query, params):
        if self.is_postgres:
            query = query.replace("?", "%s")
            return self.connection.cursor().executemany(query, params)
        return self.connection.executemany(query, params)

    def executescript(self, script):
        if self.is_postgres:
            cursor = self.connection.cursor()
            # PostgreSQL can execute the whole schema script in one call.
            cursor.execute(script)
            cursor.close()
        else:
            self.connection.executescript(script)

    def commit(self):
        self.connection.commit()

    def close(self):
        self.connection.close()


def get_db():
    if DATABASE_URL:
        conn = psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)
        return DBConnection(conn, is_postgres=True)

    conn = sqlite3.connect(SQLITE_DATABASE)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return DBConnection(conn, is_postgres=False)


def init_db():
    conn = get_db()

    schema_file = "schema_postgres.sql" if DATABASE_URL else "schema.sql"
    schema_path = os.path.join(BASE_DIR, schema_file)

    with open(schema_path, "r", encoding="utf-8") as file:
        conn.executescript(file.read())

    conn.commit()

    # Sample data is created only for local SQLite development.
    if not DATABASE_URL:
        count = conn.execute(
            "SELECT COUNT(*) AS count FROM products"
        ).fetchone()["count"]

        if count == 0:
            sample_products = [
                ("Rice 5kg", "Grocery", 20, 300, 350,
                 "ABC Distributors", "2027-06-15", 5),
                ("Milk 1L", "Dairy", 8, 50, 60,
                 "Fresh Dairy", "2026-09-02", 5),
                ("Biscuits", "Snacks", 3, 20, 30,
                 "XYZ Foods", "2026-10-20", 5),
                ("Cooking Oil 1L", "Grocery", 15, 120, 140,
                 "ABC Distributors", "2027-03-10", 5)
            ]

            conn.executemany("""
                INSERT INTO products
                (name, category, quantity, purchase_price, selling_price,
                 supplier_name, expiry_date, minimum_stock)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, sample_products)

    conn.commit()
    conn.close()


@app.route("/")
def dashboard():
    conn = get_db()
    today = date.today()
    tomorrow = today + timedelta(days=1)
    expiry_limit = today + timedelta(days=30)

    total_products = conn.execute("""
        SELECT COUNT(*) AS count FROM products
    """).fetchone()["count"]

    total_stock = conn.execute("""
        SELECT COALESCE(SUM(quantity), 0) AS total FROM products
    """).fetchone()["total"]

    low_stock = conn.execute("""
        SELECT COUNT(*) AS count
        FROM products
        WHERE quantity <= minimum_stock
    """).fetchone()["count"]

    expiring_soon = conn.execute("""
        SELECT COUNT(*) AS count
        FROM products
        WHERE expiry_date IS NOT NULL
          AND expiry_date >= ?
          AND expiry_date <= ?
    """, (today.isoformat(), expiry_limit.isoformat())).fetchone()["count"]

    expired = conn.execute("""
        SELECT COUNT(*) AS count
        FROM products
        WHERE expiry_date IS NOT NULL
          AND expiry_date < ?
    """, (today.isoformat(),)).fetchone()["count"]

    today_sales = conn.execute("""
        SELECT COALESCE(SUM(total_amount), 0) AS total
        FROM sales
        WHERE sale_date >= ?
          AND sale_date < ?
    """, (today.isoformat(), tomorrow.isoformat())).fetchone()["total"]

    recent_sales = conn.execute("""
        SELECT
            sales.sale_id,
            sales.sale_date,
            sales.total_amount,
            sales.payment_method,
            customers.name AS customer_name
        FROM sales
        LEFT JOIN customers
            ON sales.customer_id = customers.customer_id
        ORDER BY sales.sale_id DESC
        LIMIT 5
    """).fetchall()

    conn.close()

    return render_template(
        "dashboard.html",
        total_products=total_products,
        total_stock=total_stock,
        low_stock=low_stock,
        expiring_soon=expiring_soon,
        expired=expired,
        today_sales=today_sales,
        recent_sales=recent_sales
    )


@app.route("/inventory")
def inventory():
    search = request.args.get("search", "").strip()
    conn = get_db()

    if search:
        products = conn.execute("""
            SELECT *
            FROM products
            WHERE name LIKE ?
               OR category LIKE ?
            ORDER BY name
        """, (f"%{search}%", f"%{search}%")).fetchall()
    else:
        products = conn.execute("""
            SELECT * FROM products ORDER BY name
        """).fetchall()

    conn.close()

    return render_template(
        "inventory.html",
        products=products,
        search=search,
        current_date=date.today().isoformat()
    )


@app.route("/products/add", methods=["GET", "POST"])
def add_product():
    if request.method == "POST":
        name = request.form["name"].strip()
        category = request.form["category"].strip()
        quantity = int(request.form["quantity"])
        purchase_price = float(request.form["purchase_price"])
        selling_price = float(request.form["selling_price"])
        supplier_name = request.form["supplier_name"].strip()
        expiry_date = request.form["expiry_date"]
        minimum_stock = int(request.form["minimum_stock"])

        conn = get_db()

        cursor = conn.execute("""
            INSERT INTO products
            (name, category, quantity, purchase_price, selling_price,
             supplier_name, expiry_date, minimum_stock)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            RETURNING product_id
        """, (
            name, category, quantity, purchase_price, selling_price,
            supplier_name, expiry_date, minimum_stock
        ))
        product_id = cursor.fetchone()["product_id"]

        conn.execute("""
            INSERT INTO stock_transactions
            (product_id, transaction_type, quantity, notes)
            VALUES (?, ?, ?, ?)
        """, (
            product_id, "Purchase", quantity, "Initial stock added"
        ))

        conn.commit()
        conn.close()

        flash("Product added successfully!", "success")
        return redirect(url_for("inventory"))

    return render_template("add_product.html")


@app.route("/products/edit/<int:product_id>", methods=["GET", "POST"])
def edit_product(product_id):
    conn = get_db()

    product = conn.execute("""
        SELECT * FROM products WHERE product_id = ?
    """, (product_id,)).fetchone()

    if product is None:
        conn.close()
        flash("Product not found.", "danger")
        return redirect(url_for("inventory"))

    if request.method == "POST":
        name = request.form["name"].strip()
        category = request.form["category"].strip()
        quantity = int(request.form["quantity"])
        purchase_price = float(request.form["purchase_price"])
        selling_price = float(request.form["selling_price"])
        supplier_name = request.form["supplier_name"].strip()
        expiry_date = request.form["expiry_date"]
        minimum_stock = int(request.form["minimum_stock"])

        old_quantity = product["quantity"]

        conn.execute("""
            UPDATE products
            SET name = ?,
                category = ?,
                quantity = ?,
                purchase_price = ?,
                selling_price = ?,
                supplier_name = ?,
                expiry_date = ?,
                minimum_stock = ?
            WHERE product_id = ?
        """, (
            name, category, quantity, purchase_price, selling_price,
            supplier_name, expiry_date, minimum_stock, product_id
        ))

        difference = quantity - old_quantity

        if difference != 0:
            conn.execute("""
                INSERT INTO stock_transactions
                (product_id, transaction_type, quantity, notes)
                VALUES (?, ?, ?, ?)
            """, (
                product_id,
                "Adjustment",
                difference,
                "Stock adjusted while editing product"
            ))

        conn.commit()
        conn.close()

        flash("Product updated successfully!", "success")
        return redirect(url_for("inventory"))

    conn.close()
    return render_template("edit_product.html", product=product)


@app.route("/products/delete/<int:product_id>", methods=["POST"])
def delete_product(product_id):
    conn = get_db()

    conn.execute("""
        DELETE FROM products WHERE product_id = ?
    """, (product_id,))

    conn.commit()
    conn.close()

    flash("Product deleted successfully.", "success")
    return redirect(url_for("inventory"))


@app.route("/customers", methods=["GET", "POST"])
def customers():
    conn = get_db()

    if request.method == "POST":
        name = request.form["name"].strip()
        phone = request.form["phone"].strip()
        address = request.form["address"].strip()

        conn.execute("""
            INSERT INTO customers (name, phone, address)
            VALUES (?, ?, ?)
        """, (name, phone, address))

        conn.commit()
        flash("Customer added successfully!", "success")

    customers_list = conn.execute("""
        SELECT * FROM customers ORDER BY name
    """).fetchall()

    conn.close()

    return render_template(
        "customers.html",
        customers=customers_list
    )


@app.route("/sales", methods=["GET", "POST"])
def sales():
    conn = get_db()

    if request.method == "POST":
        product_id = int(request.form["product_id"])
        quantity = int(request.form["quantity"])
        customer_id = request.form.get("customer_id")
        payment_method = request.form["payment_method"]

        customer_id = None if customer_id == "" else int(customer_id)

        product = conn.execute("""
            SELECT * FROM products WHERE product_id = ?
        """, (product_id,)).fetchone()

        if product is None:
            flash("Product not found.", "danger")
            conn.close()
            return redirect(url_for("sales"))

        if quantity <= 0:
            flash("Quantity must be greater than zero.", "danger")
            conn.close()
            return redirect(url_for("sales"))

        if product["quantity"] < quantity:
            flash(
                f"Only {product['quantity']} units are available.",
                "danger"
            )
            conn.close()
            return redirect(url_for("sales"))

        total = quantity * product["selling_price"]
        payment_status = "Pending" if payment_method == "Credit" else "Paid"

        cursor = conn.execute("""
            INSERT INTO sales
            (customer_id, total_amount, payment_method, payment_status)
            VALUES (?, ?, ?, ?)
            RETURNING sale_id
        """, (
            customer_id, total, payment_method, payment_status
        ))
        sale_id = cursor.fetchone()["sale_id"]

        conn.execute("""
            INSERT INTO sale_items
            (sale_id, product_id, quantity, selling_price, subtotal)
            VALUES (?, ?, ?, ?, ?)
        """, (
            sale_id, product_id, quantity,
            product["selling_price"], total
        ))

        conn.execute("""
            UPDATE products
            SET quantity = quantity - ?
            WHERE product_id = ?
        """, (quantity, product_id))

        conn.execute("""
            INSERT INTO stock_transactions
            (product_id, transaction_type, quantity, notes)
            VALUES (?, ?, ?, ?)
        """, (
            product_id, "Sale", -quantity, f"Sale #{sale_id}"
        ))

        conn.commit()

        flash(
            f"Sale recorded successfully! Total: ₹{total:.2f}",
            "success"
        )

    products = conn.execute("""
        SELECT * FROM products
        WHERE quantity > 0
        ORDER BY name
    """).fetchall()

    customers_list = conn.execute("""
        SELECT * FROM customers ORDER BY name
    """).fetchall()

    recent_sales = conn.execute("""
        SELECT
            sales.*,
            customers.name AS customer_name
        FROM sales
        LEFT JOIN customers
            ON sales.customer_id = customers.customer_id
        ORDER BY sales.sale_id DESC
        LIMIT 10
    """).fetchall()

    conn.close()

    return render_template(
        "sales.html",
        products=products,
        customers=customers_list,
        recent_sales=recent_sales
    )


@app.route("/payments", methods=["GET", "POST"])
def payments():
    conn = get_db()

    if request.method == "POST":
        customer_id = int(request.form["customer_id"])
        amount = float(request.form["amount"])
        payment_method = request.form["payment_method"]
        reference = request.form["reference"].strip()

        conn.execute("""
            INSERT INTO payments
            (customer_id, amount, payment_method, reference)
            VALUES (?, ?, ?, ?)
        """, (
            customer_id, amount, payment_method, reference
        ))

        pending_sale = conn.execute("""
            SELECT sale_id
            FROM sales
            WHERE customer_id = ?
              AND payment_status = 'Pending'
            ORDER BY sale_date
            LIMIT 1
        """, (customer_id,)).fetchone()

        if pending_sale:
            conn.execute("""
                UPDATE sales
                SET payment_status = 'Paid'
                WHERE sale_id = ?
            """, (pending_sale["sale_id"],))

        conn.commit()
        flash("Payment recorded successfully!", "success")

    customers_list = conn.execute("""
        SELECT * FROM customers ORDER BY name
    """).fetchall()

    payment_history = conn.execute("""
        SELECT
            payments.*,
            customers.name AS customer_name
        FROM payments
        JOIN customers
            ON payments.customer_id = customers.customer_id
        ORDER BY payment_date DESC
    """).fetchall()

    conn.close()

    return render_template(
        "payments.html",
        customers=customers_list,
        payment_history=payment_history
    )


@app.route("/settings")
def settings():
    return render_template("settings.html")


# Initialize the database when the application starts.
# Render/Gunicorn imports this file, so initialization also happens there.
try:
    init_db()
except Exception as error:
    print(f"Database initialization failed: {error}")


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 5000)),
        debug=True
    )