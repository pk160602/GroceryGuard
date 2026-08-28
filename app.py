from flask import Flask, render_template, request, redirect, url_for, flash
import sqlite3
from datetime import datetime, date, timedelta

app = Flask(__name__)
app.secret_key = "groceryguard-secret-key"

DATABASE = "grocery_shop.db"


# -------------------------
# DATABASE CONNECTION
# -------------------------

def get_db():
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


# -------------------------
# INITIALIZE DATABASE
# -------------------------

def init_db():
    conn = get_db()

    with open("schema.sql", "r", encoding="utf-8") as file:
        conn.executescript(file.read())

    conn.commit()

    # Add sample products only if database is empty
    count = conn.execute(
        "SELECT COUNT(*) FROM products"
    ).fetchone()[0]

    if count == 0:
        sample_products = [
            (
                "Rice 5kg",
                "Grocery",
                20,
                300,
                350,
                "ABC Distributors",
                "2027-06-15",
                5
            ),
            (
                "Milk 1L",
                "Dairy",
                8,
                50,
                60,
                "Fresh Dairy",
                "2026-09-02",
                5
            ),
            (
                "Biscuits",
                "Snacks",
                3,
                20,
                30,
                "XYZ Foods",
                "2026-10-20",
                5
            ),
            (
                "Cooking Oil 1L",
                "Grocery",
                15,
                120,
                140,
                "ABC Distributors",
                "2027-03-10",
                5
            )
        ]

        conn.executemany("""
            INSERT INTO products
            (
                name,
                category,
                quantity,
                purchase_price,
                selling_price,
                supplier_name,
                expiry_date,
                minimum_stock
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, sample_products)

    conn.commit()
    conn.close()


# -------------------------
# DASHBOARD
# -------------------------

@app.route("/")
def dashboard():

    conn = get_db()

    today = date.today().isoformat()

    total_products = conn.execute("""
        SELECT COUNT(*) AS count
        FROM products
    """).fetchone()["count"]

    total_stock = conn.execute("""
        SELECT COALESCE(SUM(quantity), 0) AS total
        FROM products
    """).fetchone()["total"]

    low_stock = conn.execute("""
        SELECT COUNT(*) AS count
        FROM products
        WHERE quantity <= minimum_stock
    """).fetchone()["count"]

    expiry_limit = (
        date.today() + timedelta(days=30)
    ).isoformat()

    expiring_soon = conn.execute("""
        SELECT COUNT(*) AS count
        FROM products
        WHERE expiry_date IS NOT NULL
        AND expiry_date >= ?
        AND expiry_date <= ?
    """, (today, expiry_limit)).fetchone()["count"]

    expired = conn.execute("""
        SELECT COUNT(*) AS count
        FROM products
        WHERE expiry_date IS NOT NULL
        AND expiry_date < ?
    """, (today,)).fetchone()["count"]

    today_sales = conn.execute("""
        SELECT COALESCE(SUM(total_amount), 0) AS total
        FROM sales
        WHERE DATE(sale_date) = DATE('now')
    """).fetchone()["total"]

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


# -------------------------
# INVENTORY
# -------------------------

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
            SELECT *
            FROM products
            ORDER BY name
        """).fetchall()

    conn.close()

    return render_template(
    "inventory.html",
    products=products,
    search=search,
    current_date=date.today().isoformat()
)


# -------------------------
# ADD PRODUCT
# -------------------------

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
            (
                name,
                category,
                quantity,
                purchase_price,
                selling_price,
                supplier_name,
                expiry_date,
                minimum_stock
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            name,
            category,
            quantity,
            purchase_price,
            selling_price,
            supplier_name,
            expiry_date,
            minimum_stock
        ))

        product_id = cursor.lastrowid

        # Record initial stock
        conn.execute("""
            INSERT INTO stock_transactions
            (
                product_id,
                transaction_type,
                quantity,
                notes
            )
            VALUES (?, ?, ?, ?)
        """, (
            product_id,
            "Purchase",
            quantity,
            "Initial stock added"
        ))

        conn.commit()
        conn.close()

        flash("Product added successfully!", "success")

        return redirect(url_for("inventory"))

    return render_template("add_product.html")


# -------------------------
# EDIT PRODUCT
# -------------------------

@app.route("/products/edit/<int:product_id>", methods=["GET", "POST"])
def edit_product(product_id):

    conn = get_db()

    product = conn.execute("""
        SELECT *
        FROM products
        WHERE product_id = ?
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
            SET
                name = ?,
                category = ?,
                quantity = ?,
                purchase_price = ?,
                selling_price = ?,
                supplier_name = ?,
                expiry_date = ?,
                minimum_stock = ?
            WHERE product_id = ?
        """, (
            name,
            category,
            quantity,
            purchase_price,
            selling_price,
            supplier_name,
            expiry_date,
            minimum_stock,
            product_id
        ))

        difference = quantity - old_quantity

        if difference != 0:
            conn.execute("""
                INSERT INTO stock_transactions
                (
                    product_id,
                    transaction_type,
                    quantity,
                    notes
                )
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

    return render_template(
        "edit_product.html",
        product=product
    )


# -------------------------
# DELETE PRODUCT
# -------------------------

@app.route("/products/delete/<int:product_id>", methods=["POST"])
def delete_product(product_id):

    conn = get_db()

    conn.execute("""
        DELETE FROM products
        WHERE product_id = ?
    """, (product_id,))

    conn.commit()
    conn.close()

    flash("Product deleted successfully.", "success")

    return redirect(url_for("inventory"))


# -------------------------
# CUSTOMERS
# -------------------------

@app.route("/customers", methods=["GET", "POST"])
def customers():

    conn = get_db()

    if request.method == "POST":

        name = request.form["name"].strip()
        phone = request.form["phone"].strip()
        address = request.form["address"].strip()

        conn.execute("""
            INSERT INTO customers
            (
                name,
                phone,
                address
            )
            VALUES (?, ?, ?)
        """, (
            name,
            phone,
            address
        ))

        conn.commit()

        flash("Customer added successfully!", "success")

    customers_list = conn.execute("""
        SELECT *
        FROM customers
        ORDER BY name
    """).fetchall()

    conn.close()

    return render_template(
        "customers.html",
        customers=customers_list
    )


# -------------------------
# SALES
# -------------------------

@app.route("/sales", methods=["GET", "POST"])
def sales():

    conn = get_db()

    if request.method == "POST":

        product_id = int(request.form["product_id"])
        quantity = int(request.form["quantity"])
        customer_id = request.form.get("customer_id")
        payment_method = request.form["payment_method"]

        if customer_id == "":
            customer_id = None
        else:
            customer_id = int(customer_id)

        product = conn.execute("""
            SELECT *
            FROM products
            WHERE product_id = ?
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

        payment_status = (
            "Pending"
            if payment_method == "Credit"
            else "Paid"
        )

        cursor = conn.execute("""
            INSERT INTO sales
            (
                customer_id,
                total_amount,
                payment_method,
                payment_status
            )
            VALUES (?, ?, ?, ?)
        """, (
            customer_id,
            total,
            payment_method,
            payment_status
        ))

        sale_id = cursor.lastrowid

        conn.execute("""
            INSERT INTO sale_items
            (
                sale_id,
                product_id,
                quantity,
                selling_price,
                subtotal
            )
            VALUES (?, ?, ?, ?, ?)
        """, (
            sale_id,
            product_id,
            quantity,
            product["selling_price"],
            total
        ))

        conn.execute("""
            UPDATE products
            SET quantity = quantity - ?
            WHERE product_id = ?
        """, (
            quantity,
            product_id
        ))

        conn.execute("""
            INSERT INTO stock_transactions
            (
                product_id,
                transaction_type,
                quantity,
                notes
            )
            VALUES (?, ?, ?, ?)
        """, (
            product_id,
            "Sale",
            -quantity,
            f"Sale #{sale_id}"
        ))

        conn.commit()

        flash(
            f"Sale recorded successfully! Total: ₹{total:.2f}",
            "success"
        )

    products = conn.execute("""
        SELECT *
        FROM products
        WHERE quantity > 0
        ORDER BY name
    """).fetchall()

    customers_list = conn.execute("""
        SELECT *
        FROM customers
        ORDER BY name
    """).fetchall()

    recent_sales = conn.execute("""
        SELECT
            sales.*,
            customers.name AS customer_name
        FROM sales
        LEFT JOIN customers
            ON sales.customer_id = customers.customer_id
        ORDER BY sale_id DESC
        LIMIT 10
    """).fetchall()

    conn.close()

    return render_template(
        "sales.html",
        products=products,
        customers=customers_list,
        recent_sales=recent_sales
    )


# -------------------------
# PAYMENTS
# -------------------------

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
            (
                customer_id,
                amount,
                payment_method,
                reference
            )
            VALUES (?, ?, ?, ?)
        """, (
            customer_id,
            amount,
            payment_method,
            reference
        ))

        # Find oldest pending credit sale
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
        SELECT *
        FROM customers
        ORDER BY name
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


# -------------------------
# SETTINGS
# -------------------------

@app.route("/settings")
def settings():
    return render_template("settings.html")


# -------------------------
# START APPLICATION
# -------------------------

if __name__ == "__main__":
    init_db()

    app.run(
        debug=True
    )