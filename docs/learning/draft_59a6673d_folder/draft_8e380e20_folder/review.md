# Page 01 内容复查

- 主模式：Knowledge / Explanation；读者为已学普通下单的初学者。
- 学习产出：区分请求幂等、活动限购、库存原子更新；解释原 Key 优先和关闭后重购。
- 业务示例：89 元、地址 A/B、K1/K2、O1/O2；例子与真实验证明确分开。
- 源码核对：promotions/services.py 27-33、66-85、111-178；models.py 59-114。
- 证据核对：6A 2026-10-07 真 MySQL；6D 2026-10-08 API；未写成本轮重跑。
- 约束：无业务代码、数据、配置、Git 或飞书远端修改；无 Lua/MQ 秒杀下单已实现宣称。
- 图：幂等/资格时序的白板占位 1；Mermaid/OpenAPI/PNG 本地稿配套。
- XML：唯一 title；正文 h1 全部 seq=auto；合法 table/pre；导航由根代理补齐。
- 停止条件：能够独立说明三层分别保护什么、原 Key 和新 Key 区别、方案代价。
- CLI Profile Check: assessment.status=passed；word_count=1654（不设硬字数门槛）。图 29 nodes/18 connectors，--check errors=0 warnings=0；view_image 已目检，三个分支与嵌套资格判断可读，无重叠。
- 图最终修订加入重放/异参/限购三个提前返回的事务结束与释放锁；重新 render、--check 和 view_image 均完成，无视觉问题。
