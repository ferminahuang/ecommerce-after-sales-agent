# 第 2 轮改动说明（v2）

v1 的文件都保留：system_prompt.md、测试用例.csv/.xlsx、测试用例_评测结果.xlsx、eval/*；v1 版本的后端和 OpenAPI 备份在 v1_backup/。

| 修复 | 改动 | 文件 |
|---|---|---|
| 1 直接建单 | 方案唯一、≤50 元或用户已表明偏好时直接建单；方案有多个时才让用户选。测试脚本给 TC04、TC06 加了用户选择轮，给 TC09、TC10 加了“坚持”轮 | system_prompt_v2.md、测试用例_v2.csv |
| 2 风险信号范围 | 薅羊毛只在退款或赔付诉求时判断；新增工具 urge_logistics：催件，停滞超过 3 天自动发 3 元券 | system_prompt_v2.md、backend/main.py |
| 3 时效 | get_policy 新增 sla 字段，工单和催件的返回也带 sla；提示词要求只能引用 sla，删掉了示例里的“48 小时”（生鲜“48 小时内坏损”是政策规则，保留） | mock_data.json、system_prompt_v2.md |
| 4 后端硬校验 | create_after_sale_ticket 按政策校验，不合规返回 422（高金额、退款类且售后≥3 次、超过售后期、未发货却办退货、无佐证补发、生鲜规则、>50 元仅退款）。**选的方案：建单时自动通知商家**（非转人工工单），notify_merchant 只用于补充说明；转人工工单调用 notify_merchant 返回 422；404 信息里写明 ticket_id 必须用建单的返回值 | backend/main.py |
| 5 两步转人工 | 超出政策时第一次只解释，用户坚持才转人工 | system_prompt_v2.md |

OpenAPI：新增 `/logistics/{order_id}/urge`（urge_logistics），create_after_sale_ticket 新增 `merchant_suggestion` 参数和 422 响应，描述也同步更新了。openapi_live.yaml（本地部署用，未包含在仓库中）和 openapi_v2.yaml（占位地址）都已更新。
