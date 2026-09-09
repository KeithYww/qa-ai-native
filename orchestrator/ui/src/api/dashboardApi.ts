// SPDX-FileCopyrightText: 2025-2026 Taras Paruta (partarstu@gmail.com)
//
// SPDX-License-Identifier: AGPL-3.0-only

import { isAxiosError } from 'axios';

import { apiClient } from './client';
import type { DashboardSummary, AgentInfo, TaskInfo, ErrorInfo, LogEntry, TraceMessage } from '../types/dashboard';

/**
 * Dashboard API client for fetching orchestrator state.
 */
export const dashboardApi = {
  /**
   * Get high-level dashboard statistics.
   */
  async getSummary(): Promise<DashboardSummary> {
    const response = await apiClient.get<DashboardSummary>('/summary');
    return response.data;
  },

  /**
   * Get detailed status of all registered agents.
   */
  async getAgents(): Promise<AgentInfo[]> {
    const response = await apiClient.get<AgentInfo[]>('/agents');
    return response.data;
  },

  /**
   * Get recent tasks with their details.
   */
  async getTasks(limit: number = 50): Promise<TaskInfo[]> {
    const response = await apiClient.get<TaskInfo[]>('/tasks', {
      params: { limit },
    });
    return response.data;
  },

  /**
   * Get recent errors with context.
   */
  async getErrors(limit: number = 20): Promise<ErrorInfo[]> {
    const response = await apiClient.get<ErrorInfo[]>('/errors', {
      params: { limit },
    });
    return response.data;
  },

  /**
   * Get recent application logs.
   */
  async getLogs(limit: number = 100, offset: number = 0, level?: string, taskId?: string, agentId?: string): Promise<LogEntry[]> {
    const response = await apiClient.get<LogEntry[]>('/logs', {
      params: { limit, offset, level, task_id: taskId, agent_id: agentId },
    });
    return response.data;
  },

  /**
   * Get the redacted debug trace for a task. Returns null if the task has no trace yet
   * (e.g. still running, or the agent produced none).
   */
  async getTaskTrace(taskId: string): Promise<TraceMessage[] | null> {
    try {
      const response = await apiClient.get<TraceMessage[]>(`/tasks/${taskId}/trace`);
      return response.data;
    } catch (error) {
      if (isAxiosError(error) && error.response?.status === 404) {
        return null;
      }
      throw error;
    }
  },

  /**
   * Manually trigger agent discovery.
   */
  async triggerDiscovery(): Promise<{ message: string }> {
    const response = await apiClient.post<{ message: string }>('/discovery');
    return response.data;
  },
};
