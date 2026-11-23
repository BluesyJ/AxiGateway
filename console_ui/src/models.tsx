import { localApi, Value } from "./monitor";
import { useEffect, useState, type FormEvent } from "react";
import {
  Plus,
  Send,
  LoaderCircle,
  Pencil,
  Trash2,
  MessageSquare,
  Server,
  Check,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";

type Model = {
  id: string;
  name: string;
  model: string;
  base_url: string;
  revision: number;
  has_key: boolean;
};
type Message = { role: "user" | "assistant"; content: string };
type Draft = { name: string; model: string; base_url: string; key: string };
const blank: Draft = { name: "", model: "", base_url: "", key: "" };
const errors: Record<string, string> = {
  model_store_unavailable:
    "模型配置无法读取或保存，请检查当前 Windows 账户和本机目录。",
  invalid_model_config: "请填写显示名称、接口地址和模型 ID。",
  invalid_model_endpoint:
    "请填写 API 基础地址，例如 https://api.example.com/v1，不含 /chat/completions、查询参数或凭据。本地服务可用回环 HTTP 地址。",
  model_key_required: "远程模型需要 API Key。本地免认证服务可留空。",
  new_endpoint_requires_key:
    "接口地址已变更，请重新填写 API Key，旧 Key 不会发送到新地址。",
  revision_conflict: "配置已被其他页面修改，请刷新后重新编辑。",
  model_configuration_changed:
    "模型配置已变更，本次没有发送消息。请刷新并新建对话后继续。",
  model_not_found: "该模型已被删除，请刷新模型列表。",
  model_limit_reached: "最多保存 32 个模型，请先删除不再使用的配置。",
  model_connection_failed:
    "未收到模型服务的可用结果，调用是否已执行未知。请检查接口地址和网络后再决定是否重发。",
  model_proxy_start_failed: "模型网关启动失败，请检查本机运行环境。",
  upstream_auth_failed: "上游拒绝了身份验证，请检查 API Key。",
  upstream_access_denied: "上游拒绝访问，请检查该 Key 的模型权限。",
  upstream_rate_limited: "上游返回限流或额度错误，请检查服务商账户。",
  upstream_request_failed:
    "模型服务返回错误，请检查模型 ID、接口地址及服务商状态。",
  model_no_text_reply:
    "模型没有返回可显示的文字回复。可调整最大输出 token 后再试。",
  invalid_chat_request: "对话内容或输出上限不符合要求，请新建对话后重试。",
  request_too_large: "对话上下文过长，请新建对话后继续。",
};
async function api(path: string, method = "GET", data?: object) {
  const response = await fetch("/api/local/" + path, {
    method,
    headers: {
      "X-Playground": "1",
      ...(data ? { "Content-Type": "application/json" } : {}),
    },
    ...(data ? { body: JSON.stringify(data) } : {}),
  });
  const value = await response.json();
  if (!response.ok)
    throw new Error(
      errors[value.error] ?? `请求失败（HTTP ${response.status}）。`,
    );
  return value;
}
const failed = (e: unknown) =>
  e instanceof Error && e.message !== "Failed to fetch"
    ? e.message
    : "浏览器未收到结果，操作是否已执行未知。恢复连接后刷新查看。";

export function ModelWorkspace({
  view,
  refresh,
  goChat,
  goRecords,
}: {
  view: "models" | "chat";
  refresh: number;
  goChat: () => void;
  goRecords: () => void;
}) {
  const [models, setModels] = useState<Model[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [editor, setEditor] = useState<Model | "new" | null>(null);
  const [draft, setDraft] = useState<Draft>(blank);
  const [saving, setSaving] = useState(false);
  const [deleting, setDeleting] = useState<string | null>(null);
  const [selected, setSelected] = useState("");
  const [messages, setMessages] = useState<Message[]>([]);
  const [prompt, setPrompt] = useState("");
  const [maxTokens, setMaxTokens] = useState(512);
  const [sending, setSending] = useState(false);
  const [chatError, setChatError] = useState("");
  const [saveContent, setSaveContent] = useState(false);
  const [recordNotice, setRecordNotice] = useState("");
  const [lastCall, setLastCall] = useState<{
    model: string;
    id: string;
  } | null>(null);
  const [usage, setUsage] = useState<{
    prompt_tokens: number | null;
    completion_tokens: number | null;
    total_tokens: number | null;
    usage_source: string;
  } | null>(null);
  useEffect(() => {
    let active = true;
    let timer: ReturnType<typeof setTimeout>;
    setUsage(null);
    async function read(attempt: number) {
      if (!lastCall) return;
      try {
        const value = await localApi(
          `records/${lastCall.model}/${lastCall.id}`,
        );
        if (!active) return;
        setUsage(value.record);
        if (value.record.total_tokens == null && attempt < 2)
          timer = setTimeout(() => void read(attempt + 1), 700);
      } catch {
        if (active && attempt < 2)
          timer = setTimeout(() => void read(attempt + 1), 700);
      }
    }
    void read(0);
    return () => {
      active = false;
      clearTimeout(timer);
    };
  }, [lastCall]);
  const [elapsed, setElapsed] = useState<number | null>(null);

  async function load() {
    if (sending) return;
    setLoading(true);
    setError("");
    try {
      const data = await api("models");
      setModels(data.models);
      setSelected((current) =>
        data.models.some((m: Model) => m.id === current)
          ? current
          : (data.models[0]?.id ?? ""),
      );
    } catch (e) {
      setError(failed(e));
    } finally {
      setLoading(false);
    }
  }
  useEffect(() => {
    void load();
  }, [refresh]);
  const model = models.find((m) => m.id === selected);
  useEffect(() => {
    setMessages([]);
    setChatError("");
    setElapsed(null);
    setLastCall(null);
    setSaveContent(false);
    setRecordNotice("");
  }, [selected, model?.revision]);
  function edit(value: Model | "new") {
    setEditor(value);
    setError("");
    setNotice("");
    setDraft(
      value === "new"
        ? blank
        : {
            name: value.name,
            model: value.model,
            base_url: value.base_url,
            key: "",
          },
    );
  }
  async function save(e: FormEvent) {
    e.preventDefault();
    if (!editor || sending || saving) return;
    setSaving(true);
    setError("");
    setNotice("");
    try {
      const saved = await api(
        editor === "new" ? "models" : "models/" + editor.id,
        editor === "new" ? "POST" : "PUT",
        { ...draft, expected_revision: editor === "new" ? 0 : editor.revision },
      );
      setDraft(blank);
      setEditor(null);
      setMessages([]);
      setSelected(saved.id);
      await load();
      setNotice("模型已保存，可以开始对话。保存过程没有调用模型。");
    } catch (e) {
      setError(failed(e));
    } finally {
      setSaving(false);
    }
  }
  async function remove(value: Model) {
    if (sending || saving) return;
    setSaving(true);
    setError("");
    try {
      await api("models/" + value.id, "DELETE", {
        expected_revision: value.revision,
      });
      setDeleting(null);
      await load();
      setNotice("已删除本机模型配置。");
    } catch (e) {
      setError(failed(e));
    } finally {
      setSaving(false);
    }
  }
  async function send(e: FormEvent) {
    e.preventDefault();
    if (!model || !prompt.trim() || sending) return;
    const next: Message[] = [...messages, { role: "user", content: prompt }];
    setSending(true);
    setChatError("");
    setElapsed(null);
    try {
      const data = await api("chat", "POST", {
        model: model.id,
        revision: model.revision,
        messages: next,
        max_tokens: maxTokens,
        save_content: saveContent,
      });
      setMessages([...next, { role: "assistant", content: data.reply }]);
      setPrompt("");
      setElapsed(data.duration_ms);
      setLastCall({ model: model.id, id: data.call_id });
      setRecordNotice(
        data.recording_failed
          ? "调用已完成，但记录保存失败。"
          : data.content_status === "save_failed"
            ? "调用已完成，但正文保存失败。"
            : data.content_status === "saved"
              ? "本次请求与回复已在本机加密保存。"
              : "本次正文未保存。",
      );
    } catch (e) {
      setChatError(failed(e));
    } finally {
      setSending(false);
      setSaveContent(false);
    }
  }
  return (
    <>
      {error && (
        <Card className="pg-feedback" role="alert">
          <strong>模型配置操作失败</strong>
          <p>{error}</p>
          <Button onClick={() => void load()}>重新加载模型</Button>
        </Card>
      )}
      {notice && (
        <div className="pg-notice" role="status">
          <Check size={16} />
          {notice}
        </div>
      )}
      {loading && (
        <div className="pg-empty">
          <LoaderCircle className="pg-spin" />
          <p>正在读取本机模型…</p>
        </div>
      )}
      {view === "models" && (
        <>
          <div className="pg-row pg-model-toolbar">
            <p>添加 OpenAI 兼容服务，保存后在模型对话中选择。</p>
            <Button onClick={() => edit("new")} disabled={saving || sending}>
              <Plus size={16} />
              添加模型
            </Button>
          </div>
          {editor && (
            <Card className="pg-model-editor">
              <form onSubmit={save}>
                <h2>{editor === "new" ? "添加模型" : "编辑模型"}</h2>
                <div className="pg-model-fields">
                  <label className="pg-field">
                    显示名称
                    <input
                      required
                      maxLength={64}
                      value={draft.name}
                      placeholder="例如：我的 DeepSeek"
                      onChange={(e) =>
                        setDraft({ ...draft, name: e.target.value })
                      }
                    />
                  </label>
                  <label className="pg-field">
                    模型 ID
                    <input
                      required
                      maxLength={160}
                      value={draft.model}
                      placeholder="服务商提供的模型名称"
                      onChange={(e) =>
                        setDraft({ ...draft, model: e.target.value })
                      }
                    />
                  </label>
                  <label className="pg-field">
                    API Base URL
                    <input
                      required
                      type="url"
                      aria-label="API Base URL"
                      maxLength={2048}
                      value={draft.base_url}
                      placeholder="https://api.example.com/v1"
                      onChange={(e) =>
                        setDraft({ ...draft, base_url: e.target.value })
                      }
                    />
                    <small>填写基础地址，不附加 /chat/completions。</small>
                  </label>
                  <label className="pg-field">
                    API Key
                    <input
                      type="password"
                      aria-label="API Key"
                      autoComplete="new-password"
                      maxLength={4096}
                      value={draft.key}
                      placeholder={
                        editor === "new"
                          ? "本地免认证服务可留空"
                          : "留空保留原 Key；更换地址时重新填写"
                      }
                      onChange={(e) =>
                        setDraft({ ...draft, key: e.target.value })
                      }
                    />
                    <small>在本机加密保存，保存后不回显。</small>
                  </label>
                </div>
                <div className="pg-row pg-model-actions">
                  <Button
                    type="button"
                    onClick={() => {
                      setEditor(null);
                      setDraft(blank);
                    }}
                    disabled={saving}
                  >
                    取消
                  </Button>
                  <Button type="submit" disabled={saving}>
                    {saving ? "正在保存…" : "保存模型"}
                  </Button>
                </div>
              </form>
            </Card>
          )}
          {!loading && !error && !models.length && (
            <div className="pg-empty">
              <Server size={28} />
              <p>还没有添加模型。添加后即可开始对话。</p>
            </div>
          )}
          <div className="pg-model-list">
            {models.map((item) => (
              <Card key={item.id} className="pg-model-card">
                <div className="pg-row">
                  <Server size={22} />
                  <div>
                    <h3>{item.name}</h3>
                    <code>{item.model}</code>
                  </div>
                  <span className="pg-tag">
                    {item.has_key ? "Key 已配置" : "无需 Key"}
                  </span>
                </div>
                <p className="pg-endpoint">{item.base_url}</p>
                <div className="pg-row pg-model-actions">
                  <Button
                    onClick={() => {
                      setSelected(item.id);
                      goChat();
                    }}
                    disabled={sending}
                  >
                    <MessageSquare size={15} />
                    开始对话
                  </Button>
                  <Button
                    onClick={() => edit(item)}
                    disabled={saving || sending}
                  >
                    <Pencil size={15} />
                    编辑
                  </Button>
                  <Button
                    onClick={() => setDeleting(item.id)}
                    disabled={saving || sending}
                  >
                    <Trash2 size={15} />
                    删除
                  </Button>
                </div>
                {deleting === item.id && (
                  <div className="pg-delete-confirm">
                    <p>
                      删除“{item.name}”的本机配置及保存的
                      Key？已采集的调用元数据保留。
                    </p>
                    <Button onClick={() => void remove(item)} disabled={saving}>
                      确认删除
                    </Button>
                    <Button onClick={() => setDeleting(null)}>取消删除</Button>
                  </div>
                )}
              </Card>
            ))}
          </div>
        </>
      )}
      {view === "chat" && (
        <Card className="pg-chat-card">
          <div className="pg-chat-controls">
            <label className="pg-field">
              对话模型
              <select
                aria-label="对话模型"
                value={selected}
                onChange={(e) => setSelected(e.target.value)}
                disabled={sending || !models.length}
              >
                <option value="" disabled>
                  请选择已添加的模型
                </option>
                {models.map((m) => (
                  <option key={m.id} value={m.id}>
                    {m.name} · {m.model}
                  </option>
                ))}
              </select>
            </label>
            <label className="pg-field">
              最大输出 token
              <input
                type="number"
                min={1}
                max={4096}
                value={maxTokens}
                onChange={(e) => setMaxTokens(Number(e.target.value))}
                disabled={sending}
              />
            </label>
            <Button
              onClick={() => {
                setMessages([]);
                setChatError("");
                setElapsed(null);
                setLastCall(null);
                setRecordNotice("");
              }}
              disabled={sending}
            >
              新建对话
            </Button>
          </div>
          {model && (
            <div className="pg-chat-destination">
              消息发送至 <strong>{model.base_url}</strong> · 本机 Playground
              测试入口
            </div>
          )}
          <div className="pg-messages" aria-label="对话消息" aria-live="polite">
            {!messages.length && (
              <div className="pg-empty">
                <MessageSquare size={28} />
                <p>
                  {models.length
                    ? "选择模型，发送第一条消息。首次调用需要启动模型网关。"
                    : "请先在「我的模型」中添加一个模型。"}
                </p>
              </div>
            )}
            {messages.map((m, i) => (
              <div key={i} className={"pg-message " + m.role}>
                <strong>{m.role === "user" ? "你" : model?.name}</strong>
                <p>{m.content}</p>
              </div>
            ))}
            {sending && (
              <div className="pg-message">
                <LoaderCircle className="pg-spin" size={16} /> 正在等待模型回复…
              </div>
            )}
          </div>
          {chatError && (
            <div className="pg-feedback" role="alert">
              <p>{chatError}</p>
              <small>输入已保留，未自动重发。</small>
            </div>
          )}
          {elapsed != null && (
            <small className="pg-chat-timing">
              本次耗时 {elapsed} ms（包含首次启动耗时）
            </small>
          )}
          {lastCall && (
            <div className="monitor-chat-usage">
              <span>
                输入 <Value value={usage?.prompt_tokens} /> · 输出{" "}
                <Value value={usage?.completion_tokens} /> · 总 token{" "}
                <Value value={usage?.total_tokens} /> · 来源{" "}
                {usage?.usage_source ?? "等待入库"}
              </span>
              <Button onClick={goRecords}>查看调用记录</Button>
            </div>
          )}
          {recordNotice && <p className="pg-chat-timing">{recordNotice}</p>}
          <form onSubmit={send} className="pg-chat-compose">
            <label className="pg-field">
              消息
              <textarea
                aria-label="消息"
                rows={3}
                maxLength={4096}
                value={prompt}
                onChange={(e) => setPrompt(e.target.value)}
                placeholder="输入消息…"
                disabled={sending}
              />
            </label>
            <label className="monitor-consent">
              <input
                type="checkbox"
                checked={saveContent}
                onChange={(e) => setSaveContent(e.target.checked)}
                disabled={sending}
              />
              保存本次对话内容（包含上下文与回复，本机加密）
            </label>
            <div className="pg-row">
              <small>
                {messages.length >= 32
                  ? "本次对话已达 16 轮，请新建对话。"
                  : "发送将调用所选服务。未勾选则不保存正文；刷新清空当前对话窗口。"}
              </small>
              <Button
                type="submit"
                disabled={
                  sending ||
                  !model ||
                  !prompt.trim() ||
                  !Number.isInteger(maxTokens) ||
                  maxTokens < 1 ||
                  maxTokens > 4096 ||
                  messages.length >= 32
                }
              >
                <Send size={16} />
                {sending ? "等待回复…" : "发送消息"}
              </Button>
            </div>
          </form>
        </Card>
      )}
    </>
  );
}
