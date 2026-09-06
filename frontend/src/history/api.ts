import { requestJson } from "../lib/api";
import { API_BASE_URL } from "../lib/config";
import type { ThreadHistoryResponse } from "./types";

export function getThreadHistory(threadId: string): Promise<ThreadHistoryResponse> {
  return requestJson<ThreadHistoryResponse>(
    `${API_BASE_URL}/api/threads/${encodeURIComponent(threadId)}/history`,
  );
}
