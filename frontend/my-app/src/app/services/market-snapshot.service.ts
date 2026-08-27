import { Injectable, inject } from '@angular/core';
import { HttpClient, HttpParams } from '@angular/common/http';
import { Observable } from 'rxjs';

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
  private readonly baseUrl = 'http://localhost:8000';

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
}
