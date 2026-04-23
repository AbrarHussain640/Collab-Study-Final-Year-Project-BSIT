from flask import Flask, render_template


app = Flask(__name__)


@app.route("/")
def Wellcome():
    return render_template("Wellcome.html")

@app.route("/login")
def login():
    return render_template("login.html")

@app.route("/forgotPassword")
def forgetPassword():
    return render_template("forgotPassword.html")

@app.route("/register")
def register():
    return render_template("register.html")

@app.route("/admin_dashboard")
def admin_dashboard():
    return render_template("admin_dashboard.html")


if __name__ == "__main__":
    app.run(debug=True)