import { Injectable, inject } from '@angular/core';
import { HttpClient, HttpParams } from '@angular/common/http';
import { Observable } from 'rxjs';
import { environment } from '../../environments/environment';

export interface MarketTopicSnapshot {
  id: number;
  snapshot_time: string;
  topic_name: string;
  trend: string;
  topic_score: number | null;
  stock_list: { id: string; name: string }[];
  reason: string[] | null;
  created_at: string | null;
}

export interface MarketSnapshot {
  id: number;
  snapshot_time: string;
  market_score: number;
  trend: string;
  up_count: number;
  down_count: number;
  limit_up: number;
  limit_down: number;
  foreign_buy: number;
  investment_buy: number;
  dealer_buy: number;
  total_volume: number;
  margin_balance: number;
  short_sell_balance: number;
  industry_diffusion: number;
  speculation_index: number;
  retail_confidence: number;
  source_status: string;
  analysis_version: string;
  created_at: string | null;
  updated_at: string | null;
}

@Injectable({ providedIn: 'root' })
export class MarketSnapshotService {
  private readonly http = inject(HttpClient);
  private readonly baseUrl = environment.apiBaseUrl;

  /** 查指定日期快照，省略則預設今日；無資料時 API 回傳 [] */
  getByDate(targetDate?: string): Observable<MarketSnapshot | []> {
    let params = new HttpParams();
    if (targetDate) params = params.set('target_date', targetDate);
    return this.http.get<MarketSnapshot | []>(`${this.baseUrl}/market/snapshot`, { params });
  }

  /** 取最新一筆快照 */
  getLatest(): Observable<MarketSnapshot> {
    return this.http.get<MarketSnapshot>(`${this.baseUrl}/market/snapshot/latest`);
  }

  /** 取最新一批熱門主題（依排名排序） */
  getLatestTopics(): Observable<MarketTopicSnapshot[]> {
    return this.http.get<MarketTopicSnapshot[]>(`${this.baseUrl}/market/topics/latest`);
  }
}
