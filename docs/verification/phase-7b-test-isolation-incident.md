# Phase 7B测试隔离事故与恢复记录

2026-10-10，Asia/Shanghai。[本片验证](phase-7b-search-index-prototype.md) · [指南](../learning/phase-7b-search-index-prototype-study-guide.md)。本记录保存事故事实，不把它包装成项目新增能力。

## 原因与影响

首轮在线测试15:16左右，guard为了验证拒绝开发库，将真实Django connection.settings_dict.NAME临时改成project_one，再进入CaptureQueriesContext。前一transaction=True测试已关闭连接；上下文会ensure_connection，此时连接开发库。恢复NAME只改配置，不会切换活连接；pytest随后flush按实际DATABASE()枚举表并DELETE，造成误清。捕获“0条商品SQL”没有包含连接初始化或teardown，不能证明安全。

只读SHOW BINLOG EVENTS与宿主官方mysqlbinlog8.4.10校验解码确认：binlog.000025的开发库Delete_rows只有django_session、auth_permission、accounts_user、django_content_type；FULL before-image分别1、104、1、26条。其他开发业务表没有该次删除行事件；开发schema仍30表，迁移记录35条。详细行值未输出。

首轮[raw](phase-7b-search-index-prototype-online.txt)和[JSON](phase-7b-search-index-prototype-online.json)保留原始通过结果，不能作为隔离正确性证据。错误由测试实现及运行前审查遗漏造成，不是用户操作。

## 修正与恢复

1. 暂停后续数据库测试，只读确认影响；复制原始binlog到Git外私密目录，校验成功。没有轮转、清理或改写服务器日志。
2. guard改为只替换search_prototype模块的connection引用为配置替身，不改变真实连接；正常fixture及来源核对实际SELECT DATABASE为test_project_one。
3. 核对139条before-image与当前列形状、列数及SQL字面量限制。伪SQL解码不是可直接重放的恢复脚本；候选将值转成参数，再参数化插入。[MySQL官方边界](https://dev.mysql.com/doc/refman/8.4/en/mysqlbinlog-row-events.html)
4. 两次可销毁test_project_one演练均逐字段相同，测试库销毁，开发4表快照不变。第二次验证恢复安全门：InnoDB/主键形状、拒绝优化模式、SERIALIZABLE单事务、锁后再核对快照、只删除捕获的明确主键、异常回滚。
5. 拥有者明确答复“允许按已验证方案恢复（推荐）”后，执行同一候选开发恢复。只修改上述4表；替换自动重新生成的权限/内容类型，补回原用户/Session，不重新创建账号或修改密码。
6. 提交后通过新连接再次只读核对139条全部字段与删除前一致；保存的Session能解析到原用户。没有进行浏览器登录操作；4表自增计数不作倒退，不能宣称全部数据库元数据还原。

原始binlog、带值解码、删除前记录、事故后备份和恢复候选均保存在`C:\Users\youyu\.codex\tmp\project-one-es-incident-20261010`，没有进入Git。公开的[脱敏恢复结果](phase-7b-test-isolation-incident.json)只含计数和校验结论。

## 收束条件

真实数据已恢复，修正后的在线5项、独立离线1项及原目录24项/6子场景通过。保留失败/事故证据；后续不得用修改真实数据库目标的办法测试拒绝分支。没有扩大为全项目测试框架重构或新增自动恢复功能。
