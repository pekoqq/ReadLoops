"""材料与蒸馏 API。

材料 = 用户自己的文本（粘贴 / 文件 / 书架导入）。
蒸馏 = 从材料里提取统计特征，产出「风格画像」，供文章生成参考。
画像里只有统计量，不含原文。
"""
from fastapi import APIRouter, HTTPException

from app.services import distill as ds
from app.services import materials as mt

router = APIRouter(prefix="/api/materials", tags=["materials"])


@router.get("")
def list_all():
    return mt.list_materials()


@router.post("/paste")
def paste(payload: dict):
    result = mt.import_text(payload.get("title", ""), payload.get("content", ""))
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("error", "导入失败"))
    return result


@router.post("/import")
def import_path(payload: dict):
    path = payload.get("path", "")
    if not path:
        raise HTTPException(status_code=400, detail="缺少文件路径")
    result = mt.import_file(path, title=payload.get("title"))
    if not result.get("ok"):
        # 解析失败也返回 200 + ok:false，前端要展示具体原因（例如缺 PDF 依赖）
        return result
    return result


@router.get("/{material_id}")
def detail(material_id: int, with_content: bool = False):
    m = mt.get_material(material_id, with_content=with_content)
    if not m:
        raise HTTPException(status_code=404, detail="材料不存在")
    return m


@router.delete("/{material_id}")
def remove(material_id: int):
    if not mt.delete_material(material_id):
        raise HTTPException(status_code=404, detail="材料不存在")
    return {"ok": True}


# ---------------------------------------------------------------- 蒸馏

@router.post("/distill")
def distill(payload: dict):
    ids = payload.get("material_ids") or []
    if not isinstance(ids, list) or not ids:
        raise HTTPException(status_code=400, detail="请选择至少一份材料")
    result = ds.distill_materials(
        [int(i) for i in ids],
        name=payload.get("name", ""),
        activate=bool(payload.get("activate", False)),
    )
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("error", "蒸馏失败"))
    return result


@router.get("/profiles/all")
def profiles():
    return ds.list_profiles()


@router.post("/profiles/{profile_id}/activate")
def activate(profile_id: int):
    if not ds.activate_profile(profile_id):
        raise HTTPException(status_code=404, detail="画像不存在")
    return {"ok": True}


@router.delete("/profiles/{profile_id}")
def remove_profile(profile_id: int):
    if not ds.delete_profile(profile_id):
        raise HTTPException(status_code=404, detail="画像不存在")
    return {"ok": True}
