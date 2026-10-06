# 医疗美容执业与项目合规服务

本项目是使用 Python、FastAPI 与 SQLite 实现的服务端应用，覆盖机构、人员资质、项目分级、执业范围、合规线索、监管处置与整改案件管理。它可在单个 Linux 应用容器内完成安装、测试、编译和接口验收，不依赖浏览器、外部数据库、缓存、消息队列或额外运行服务。

## 整改案件与评分调整

已核实违规的线索可建立整改案件（`/api/rectification/cases`）：

- 每项违规结论拆分为多条**可验收整改要求**与期限，机构按要求提交**版本化证据**（旧版本保留不可变）；
- 复核人可逐项给出 **部分通过 / 退回 / 认定复发**；只有全部要求通过的关闭复核才能关闭案件；
- 评分只通过**独立调整台账** `ScoreAdjustment`（违规扣分 / 整改恢复 / 复发扣分 / 撤销回滚 / 逾期处罚）变化，并按案件与复核记录幂等；
- 同一违规结论被多条线索引用（`/cases/{id}/link-clue`）不重复扣分；撤销错误复核会回滚逐项验收与评分调整；
- 逾期未关闭案件由 `/api/rectification/escalate-overdue` 统一升级处罚，重复执行不重复扣分；
- 机构详情 `/api/institutions/{id}/compliance-trace` 可从当前分数追溯原违规线索、整改要求、证据版本、复核决定与历次评分变化。


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
