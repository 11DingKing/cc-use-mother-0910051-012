# 医疗美容执业与项目合规服务

本项目是使用 Python、FastAPI 与 SQLite 实现的服务端应用，覆盖机构、人员资质、项目分级、执业范围、合规线索、监管处置以及整改案件全流程。它可在单个 Linux 应用容器内完成安装、测试、编译和接口验收，不依赖浏览器、外部数据库、缓存、消息队列或额外运行服务。

## 整改案件与评分调整

已核实违规线索可建立整改案件（`/api/rectifications/cases`）：

- 每项违规结论拆分为多条**可验收、有期限的整改要求**；
- 机构按**版本号提交不可变的整改证据**，复核人可作出**通过 / 部分通过 / 退回 / 认定复发**决定，只有**全部要求复核通过**案件才关闭，复发会自动重开；
- 所有评分影响（违规扣分、整改恢复、复发重扣、逾期升级、撤销冲正）均以**独立、不可变的评分调整记录**落账，撤销错误复核以对冲记录实现，不删除历史；
- 多条线索引用同一问题时通过关联同一案件共享一次扣分，**不重复扣分或加分**；
- 逾期未通过的要求在 `/api/rectifications/overdue-scan` 中自动升级为高优先级并扣分，要求通过后自动冲回；
- `/api/rectifications/traceability/institution/{id}` 提供从当前分数到原违规线索、整改证据版本、复核决定、历次评分变化的完整追溯链。

## 安装

```bash
python3 -m pip install -r requirements.txt -r requirements-dev.txt
```

## 测试

```bash
python3 seed_data.py && python3 -m pytest -q
```

## 编译

```bash
python3 -m compileall -q .
```

## 接口验收

```bash
python3 -c "from app.main import app; assert len(app.routes) > 5; print(len(app.routes))"
```

## 启动

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000
```
