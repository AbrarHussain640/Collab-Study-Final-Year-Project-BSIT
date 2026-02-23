from flask import Flask, render_template


app = Flask(__name__)


@app.route("/")
def Wellcome():
    return render_template("Wellcome.html")

@app.route("/login")
def login():
    return render_template("login.html")

if __name__ == "__main__":
    app.run(debug=True)