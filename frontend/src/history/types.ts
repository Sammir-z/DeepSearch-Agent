export interface ChatHistoryTurn {
  id: string;
  user_content: string;
  assistant_content: string;
  timestamp: string | null;
}

export interface ThreadHistoryResponse {
  thread_id: string;
  session_path: string | null;
  turns: ChatHistoryTurn[];
}
