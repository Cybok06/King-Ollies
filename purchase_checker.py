from flask import Blueprint, redirect, url_for
purchase_checker_bp = Blueprint("purchase_checker", __name__)
@purchase_checker_bp.route("/purchase_checker", methods=["GET", "POST"])
def purchase_checker():
    return redirect(url_for("checker_services.purchase"), code=303)
