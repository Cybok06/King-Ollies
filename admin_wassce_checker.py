from flask import Blueprint, redirect, url_for, session, abort
admin_wassce_checker_bp = Blueprint("admin_wassce_checker", __name__)
@admin_wassce_checker_bp.route("/admin/wassce_checker", methods=["GET", "POST"])
def admin_wassce_checker():
    if session.get("role") != "admin": abort(403)
    return redirect(url_for("checker_services.manage"), code=303)
