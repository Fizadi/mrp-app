from flask import Blueprint, request, jsonify
from sqlalchemy import desc
from app.extensions import db
from app.models.quality import QualityDefinition
from datetime import datetime

quality_api = Blueprint("quality_api", __name__)


# ---------------------------------------------------------
# GET ALL OPERATIONS
# ---------------------------------------------------------
@quality_api.route("/api/quality/operations", methods=["GET"])
def get_operations():

    operations = (
        QualityDefinition.query
        .filter_by(IsCurrentVersion=True)
        .order_by(QualityDefinition.Code)
        .all()
    )

    result = []

    for op in operations:
        result.append({
            "DefinitionID": op.DefinitionID,
            "Code": op.Code,
            "Name": op.Name,
            "DefinitionType": op.DefinitionType,
            "Category": op.Category,
            "ProductFamily": op.ProductFamily,
            "Specifications": op.Specifications,
            "SkillLevel": op.SkillLevel,
            "EstimatedTimeMinutes": op.EstimatedTimeMinutes,
            "CustomMinValue": op.CustomMinValue,
            "CustomMaxValue": op.CustomMaxValue,
            "CustomTargetValue": op.CustomTargetValue,
            "Version": op.Version,
            "IsActive": op.IsActive,
            "CreatedDate": op.CreatedDate
        })

    return jsonify(result)


# ---------------------------------------------------------
# CREATE OPERATION
# ---------------------------------------------------------
@quality_api.route("/api/quality/operations", methods=["POST"])
def create_operation():

    data = request.json

    new_op = QualityDefinition(
        Code=data.get("Code"),
        Name=data.get("Name"),
        DefinitionType=data.get("DefinitionType"),
        Category=data.get("Category"),
        ProductFamily=data.get("ProductFamily"),
        Specifications=data.get("Specifications"),
        SkillLevel=data.get("SkillLevel"),
        EstimatedTimeMinutes=data.get("EstimatedTimeMinutes"),
        CustomMinValue=data.get("CustomMinValue"),
        CustomMaxValue=data.get("CustomMaxValue"),
        CustomTargetValue=data.get("CustomTargetValue"),
        Version=1,
        IsCurrentVersion=True,
        IsActive=data.get("IsActive", True),
        CreatedDate=datetime.utcnow()
    )

    db.session.add(new_op)
    db.session.commit()

    return jsonify({
        "success": True,
        "DefinitionID": new_op.DefinitionID
    })


# ---------------------------------------------------------
# UPDATE OPERATION (WITH OPTIONAL VERSIONING)
# ---------------------------------------------------------
@quality_api.route("/api/quality/operations/<int:id>", methods=["PUT"])
def update_operation(id):

    data = request.json

    create_new_version = data.get("create_new_version", False)

    operation = QualityDefinition.query.get_or_404(id)

    # -----------------------------------------------------
    # CREATE NEW VERSION
    # -----------------------------------------------------
    if create_new_version:

        operation.IsCurrentVersion = False

        new_version = QualityDefinition(
            Code=data.get("Code"),
            Name=data.get("Name"),
            DefinitionType=data.get("DefinitionType"),
            Category=data.get("Category"),
            ProductFamily=data.get("ProductFamily"),
            Specifications=data.get("Specifications"),
            SkillLevel=data.get("SkillLevel"),
            EstimatedTimeMinutes=data.get("EstimatedTimeMinutes"),
            CustomMinValue=data.get("CustomMinValue"),
            CustomMaxValue=data.get("CustomMaxValue"),
            CustomTargetValue=data.get("CustomTargetValue"),
            Version=operation.Version + 1,
            PreviousVersionID=operation.DefinitionID,
            IsCurrentVersion=True,
            IsActive=data.get("IsActive", True),
            CreatedDate=datetime.utcnow()
        )

