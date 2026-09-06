import {
  LockOutlined,
  LoginOutlined,
  MailOutlined,
  UserAddOutlined
} from "@ant-design/icons";
import { Alert, Button, Card, Form, Input, Typography } from "antd";
import { useState } from "react";
import type { AuthPayload, AuthUser } from "../types";

interface AuthScreenProps {
  error: string;
  isSubmitting: boolean;
  onLogin: (payload: AuthPayload) => Promise<AuthUser | null>;
  onRegister: (payload: AuthPayload) => Promise<AuthUser | null>;
}

interface AuthFormValues extends AuthPayload {
  confirmPassword?: string;
}

type AuthMode = "login" | "register";

export function AuthScreen({
  error,
  isSubmitting,
  onLogin,
  onRegister
}: AuthScreenProps) {
  const [mode, setMode] = useState<AuthMode>("login");
  const [form] = Form.useForm<AuthFormValues>();
  const isRegistering = mode === "register";

  async function handleSubmit(values: AuthFormValues) {
    const payload: AuthPayload = {
      email: values.email.trim(),
      password: values.password
    };
    if (isRegistering) {
      await onRegister(payload);
    } else {
      await onLogin(payload);
    }
  }

  function switchMode(nextMode: AuthMode) {
    setMode(nextMode);
    form.resetFields();
  }

  return (
    <main className="auth-shell">
      <Card className="auth-card" bordered={false}>
        <div className="auth-brand">
          <span className="panel-kicker">DEEPSEARCH / IDENTITY</span>
          <Typography.Title level={1}>深度研搜</Typography.Title>
          <Typography.Paragraph>
            登录后，你的任务线程和长期记忆会与其他用户完全隔离。
          </Typography.Paragraph>
        </div>

        <div className="auth-mode-switch" role="tablist" aria-label="认证方式">
          <button
            aria-selected={!isRegistering}
            className={!isRegistering ? "auth-mode auth-mode--active" : "auth-mode"}
            onClick={() => switchMode("login")}
            role="tab"
            type="button"
          >
            <LoginOutlined aria-hidden />
            登录
          </button>
          <button
            aria-selected={isRegistering}
            className={isRegistering ? "auth-mode auth-mode--active" : "auth-mode"}
            onClick={() => switchMode("register")}
            role="tab"
            type="button"
          >
            <UserAddOutlined aria-hidden />
            注册
          </button>
        </div>

        {error ? <Alert className="auth-alert" message={error} showIcon type="error" /> : null}

        <Form
          autoComplete="on"
          className="auth-form"
          form={form}
          layout="vertical"
          onFinish={(values) => {
            void handleSubmit(values).catch(() => undefined);
          }}
          requiredMark={false}
        >
          <Form.Item
            label="邮箱"
            name="email"
            rules={[
              { required: true, message: "请输入邮箱" },
              { type: "email", message: "请输入有效的邮箱" }
            ]}
          >
            <Input autoComplete="email" prefix={<MailOutlined />} placeholder="name@example.com" size="large" />
          </Form.Item>

          <Form.Item
            label="密码"
            name="password"
            rules={[
              { required: true, message: "请输入密码" },
              { min: 8, message: "密码至少需要 8 位" }
            ]}
          >
            <Input.Password
              autoComplete={isRegistering ? "new-password" : "current-password"}
              prefix={<LockOutlined />}
              placeholder="至少 8 位"
              size="large"
            />
          </Form.Item>

          {isRegistering ? (
            <Form.Item
              dependencies={["password"]}
              label="确认密码"
              name="confirmPassword"
              rules={[
                { required: true, message: "请再次输入密码" },
                ({ getFieldValue }) => ({
                  validator(_, value) {
                    if (!value || getFieldValue("password") === value) {
                      return Promise.resolve();
                    }
                    return Promise.reject(new Error("两次输入的密码不一致"));
                  }
                })
              ]}
            >
              <Input.Password
                autoComplete="new-password"
                prefix={<LockOutlined />}
                placeholder="再次输入密码"
                size="large"
              />
            </Form.Item>
          ) : null}

          <Button
            block
            className="auth-submit"
            htmlType="submit"
            loading={isSubmitting}
            size="large"
            type="primary"
          >
            {isRegistering ? "创建账户并进入工作台" : "进入工作台"}
          </Button>
        </Form>

        <p className="auth-note">会话由后端 HttpOnly Cookie 管理，浏览器不会保存访问令牌。</p>
      </Card>
    </main>
  );
}
