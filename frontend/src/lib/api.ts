import { API_BASE_URL } from "./config";
import type {
  AuthPayload,
  AuthUser,
  CancelTaskResponse,
  FileListResponse,
  TaskResponse,
  ThreadListResponse,
  UploadResponse
} from "../types";

export class ApiError extends Error {
  readonly status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

function apiUrl(path: string): string {
  return `${API_BASE_URL}${path}`;
}

export async function requestJson<T>(input: RequestInfo | URL, init?: RequestInit): Promise<T> {
  const response = await fetch(input, {
    ...init,
    credentials: init?.credentials || "include"
  });
  const contentType = response.headers.get("content-type") || "";
  const payload = contentType.includes("application/json")
    ? await response.json()
    : await response.text();

  if (!response.ok) {
    const message =
      typeof payload === "object" && payload && "detail" in payload
        ? String(payload.detail)
        : `HTTP ${response.status}`;
    throw new ApiError(message, response.status);
  }

  return payload as T;
}

export async function registerUser(payload: AuthPayload): Promise<AuthUser> {
  return requestJson<AuthUser>(apiUrl("/api/auth/register"), {
    method: "POST",
    headers: {
      "Content-Type": "application/json"
    },
    body: JSON.stringify(payload)
  });
}

export async function loginUser(payload: AuthPayload): Promise<AuthUser> {
  return requestJson<AuthUser>(apiUrl("/api/auth/login"), {
    method: "POST",
    headers: {
      "Content-Type": "application/json"
    },
    body: JSON.stringify(payload)
  });
}

export async function logoutUser(): Promise<{ status: string }> {
  return requestJson<{ status: string }>(apiUrl("/api/auth/logout"), {
    method: "POST"
  });
}

export async function getCurrentUser(): Promise<AuthUser> {
  return requestJson<AuthUser>(apiUrl("/api/auth/me"));
}

export async function listThreads(): Promise<ThreadListResponse> {
  return requestJson<ThreadListResponse>(apiUrl("/api/threads"));
}

export async function startTask(query: string, threadId: string): Promise<TaskResponse> {
  return requestJson<TaskResponse>(apiUrl("/api/task"), {
    method: "POST",
    headers: {
      "Content-Type": "application/json"
    },
    body: JSON.stringify({
      query,
      thread_id: threadId
    })
  });
}

export async function cancelTask(threadId: string): Promise<CancelTaskResponse> {
  return requestJson<CancelTaskResponse>(apiUrl(`/api/task/${encodeURIComponent(threadId)}/cancel`), {
    method: "POST"
  });
}

export async function uploadSessionFiles(
  files: File[],
  threadId: string
): Promise<UploadResponse> {
  const formData = new FormData();
  formData.append("thread_id", threadId);
  files.forEach((file) => formData.append("files", file));

  return requestJson<UploadResponse>(apiUrl("/api/upload"), {
    method: "POST",
    body: formData
  });
}

export async function listSessionFiles(path: string): Promise<FileListResponse> {
  const url = new URL(apiUrl("/api/files"), window.location.origin);
  url.searchParams.set("path", path);
  return requestJson<FileListResponse>(url);
}

export function getDownloadUrl(path: string): string {
  const url = new URL(apiUrl("/api/download"), window.location.origin);
  url.searchParams.set("path", path);
  return url.toString();
}
