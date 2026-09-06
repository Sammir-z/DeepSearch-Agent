# 阶段 3：用户级长期记忆实施计划

## 目标

在已有 Redis 持久化和 MySQL 认证基础上，为每个已认证用户提供隔离的长期记忆能力。记忆统一写入 Redis Store 命名空间 `("memories", user_id)`，并通过 API 与 Agent 工具显式管理。

## 实施步骤

1. 在 `app/memory` 新增用户记忆服务：定义记忆记录、命名空间、类型白名单、长度/数量限制和敏感内容拦截；封装 Redis Store 的写入、查询、读取和删除。
2. 在 `app/memory` 新增 Agent 运行上下文 ContextVar，并在 `run_deep_agent` 中注入 `user_id`，同时把 `user_id` 放入 Agent 的 configurable runtime config。
3. 新增显式记忆工具（记住、忘记、查询），通过当前运行上下文获取用户身份，避免模型或调用方自行传入其他用户 ID。
4. 在 FastAPI 增加用户记忆创建、查询和删除接口；接口只从认证会话获取 user_id，并将校验错误和 Redis 不可用转换为明确 HTTP 状态码。
5. 将记忆工具接入主 Agent，更新 API 的任务调度调用以传递认证用户 ID，并在模块导出中保持现有风格。
6. 所有实现代码完成后，再统一执行编译、单元测试和 Docker Redis 冒烟验证。

## 设计约束

- 第一版不启用向量索引，查询先使用 Redis Store 的命名空间枚举，再进行大小写不敏感的内容/类型/来源匹配，避免新增 embedding 服务依赖。
- 只允许 `preference`、`fact`、`constraint` 三类记忆。
- 默认单条内容最多 2000 个字符、单次最多返回 20 条；密码、令牌、API Key、银行卡号、身份证号和私钥等敏感内容拒绝持久化。
- 记忆 key 由服务生成；删除时必须校验 key 格式并限定在当前用户命名空间内。
