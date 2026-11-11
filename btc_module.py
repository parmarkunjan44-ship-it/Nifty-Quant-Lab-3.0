import time, requests, json, hashlib, os
import numpy as np
import pandas as pd
DELTA_BASE='https://api.india.delta.exchange'

def fetch_delta_candles(start_dt,end_dt,symbol='BTCUSD',resolution='15m'):
    s=pd.Timestamp(start_dt); e=pd.Timestamp(end_dt)
    s=int((s.tz_localize('UTC') if s.tzinfo is None else s).timestamp()); e=int((e.tz_localize('UTC') if e.tzinfo is None else e).timestamp())
    step=900 if resolution=='15m' else 60; rows=[]; cursor=s
    while cursor<e:
        end=min(e,cursor+step*1990)
        r=requests.get(DELTA_BASE+'/v2/history/candles',params={'resolution':resolution,'symbol':symbol,'start':cursor,'end':end},headers={'Accept':'application/json'},timeout=30); r.raise_for_status(); j=r.json()
        if not j.get('success'): raise RuntimeError(f'Delta API error: {j}')
        rows.extend(j.get('result',[])); cursor=end+step
        if not j.get('result'): continue
        time.sleep(.12)
    if not rows: return pd.DataFrame(columns=['datetime','open','high','low','close','volume'])
    x=pd.DataFrame(rows); x['datetime']=pd.to_datetime(pd.to_numeric(x.time),unit='s',utc=True).dt.tz_convert('Asia/Kolkata')
    for c in ['open','high','low','close','volume']:
        if c in x: x[c]=pd.to_numeric(x[c],errors='coerce')
    return x.dropna(subset=['datetime','open','high','low','close']).sort_values('datetime').drop_duplicates('datetime').reset_index(drop=True)

def _rsi(s,n):
    d=s.diff(); up=d.clip(lower=0).ewm(alpha=1/n,adjust=False,min_periods=n).mean(); dn=(-d.clip(upper=0)).ewm(alpha=1/n,adjust=False,min_periods=n).mean()
    return 100-100/(1+up/dn.replace(0,np.nan))

def add_btc_indicators(df,ema_fast=9,ema_slow=20,rsi_period=14,stoch_period=14,smooth_k=3,smooth_d=3):
    x=df.copy().sort_values('datetime').reset_index(drop=True)
    x['ema_fast']=x.close.ewm(span=int(ema_fast),adjust=False).mean(); x['ema_slow']=x.close.ewm(span=int(ema_slow),adjust=False).mean()
    r=_rsi(x.close,int(rsi_period)); lo=r.rolling(int(stoch_period)).min(); hi=r.rolling(int(stoch_period)).max()
    raw=(r-lo)/(hi-lo).replace(0,np.nan)*100; x['stoch_k']=raw.rolling(int(smooth_k)).mean(); x['stoch_d']=x.stoch_k.rolling(int(smooth_d)).mean()
    tr=pd.concat([x.high-x.low,(x.high-x.close.shift()).abs(),(x.low-x.close.shift()).abs()],axis=1).max(axis=1)
    x['atr']=tr.ewm(alpha=1/14,adjust=False,min_periods=14).mean()
    x['long_condition']=(x.ema_fast>x.ema_slow)&(x.stoch_k>x.stoch_d)
    x['short_condition']=(x.ema_fast<x.ema_slow)&(x.stoch_k<x.stoch_d)
    x['signal']=''
    x.loc[x.long_condition&~x.long_condition.shift(1,fill_value=False),'signal']='LONG'
    x.loc[x.short_condition&~x.short_condition.shift(1,fill_value=False),'signal']='SHORT'
    return x

def btc_backtest(df,p,fee_bps=6.,slippage_bps=2.):
    x=add_btc_indicators(df,p['ema_fast'],p['ema_slow'],p['rsi_period'],p['stoch_period'],p['smooth_k'],p['smooth_d']); out=[]; i=0
    cost=(float(fee_bps)+float(slippage_bps))/10000
    while i<len(x)-1:
        side=x.iloc[i].signal
        if not side or pd.isna(x.iloc[i].atr) or x.iloc[i].atr<=0: i+=1; continue
        ei=i+1; entry=float(x.iloc[ei].open); risk=float(x.iloc[i].atr)*float(p['sl_atr'])
        if risk<=0: i+=1; continue
        stop=entry-risk if side=='LONG' else entry+risk; target=entry+risk*float(p['rr']) if side=='LONG' else entry-risk*float(p['rr'])
        xp=float(x.iloc[-1].close); xi=len(x)-1; why='End of data'
        for j in range(ei,len(x)):
            bar=x.iloc[j]; sl=(bar.low<=stop) if side=='LONG' else (bar.high>=stop); tp=(bar.high>=target) if side=='LONG' else (bar.low<=target)
            if sl: xp,xi,why=stop,j,'Stop loss'; break
            if tp: xp,xi,why=target,j,'Target'; break
            if j>ei and ((side=='LONG' and bar.signal=='SHORT') or (side=='SHORT' and bar.signal=='LONG')): xp,xi,why=float(bar.close),j,'Opposite signal'; break
        gross=xp-entry if side=='LONG' else entry-xp; costs=(abs(entry)+abs(xp))*cost; net=gross-costs
        out.append({'entry_time':x.iloc[ei].datetime,'exit_time':x.iloc[xi].datetime,'side':side,'entry':entry,'exit':xp,'stop':stop,'target':target,'gross_pnl':gross,'estimated_cost':costs,'net_pnl':net,'R_multiple':net/risk,'exit_reason':why})
        i=xi+1
    return pd.DataFrame(out)

def summarize_btc(t):
    if t is None or t.empty: return {'Trades':0,'Win rate %':0.,'Net PnL':0.,'Expectancy':0.,'Profit factor':0.,'Max DD':0.,'Total R':0.}
    p=t.net_pnl.astype(float); gp=p[p>0].sum(); gl=abs(p[p<0].sum()); c=p.cumsum(); dd=c-cummax0(c)
    return {'Trades':len(p),'Win rate %':(p>0).mean()*100,'Net PnL':p.sum(),'Expectancy':p.mean(),'Profit factor':gp/gl if gl else float('inf'),'Max DD':abs(dd.min()),'Total R':t.R_multiple.sum()}

def cummax0(s): return s.cummax().clip(lower=0)

def optimize_btc(df,n_candidates=300,fee_bps=6.,slippage_bps=2.,min_trades=30,seed=7,progress_callback=None,checkpoint_path="btc_optimizer_checkpoint.json"):
    """Random-search optimizer with periodic local checkpoints and progress callback.

    The candidate list is deterministic for a given seed/settings. A checkpoint is
    reused only when the input candles and optimizer settings match exactly.
    """
    n=len(df); a=int(n*.6); b=int(n*.8); train,valid,test=df.iloc[:a],df.iloc[a:b],df.iloc[b:]
    if min(len(train),len(valid),len(test))<100:
        raise ValueError('Need at least 500 15-minute candles for a 60/20/20 split.')
    cfg={"n_candidates":int(n_candidates),"fee_bps":float(fee_bps),"slippage_bps":float(slippage_bps),"min_trades":int(min_trades),"seed":int(seed)}
    # Fingerprint only the market columns used by the optimizer.
    fp_data=pd.util.hash_pandas_object(df[['datetime','open','high','low','close']].reset_index(drop=True),index=False).values.tobytes()
    fingerprint=hashlib.sha256(fp_data).hexdigest()
    rng=np.random.default_rng(seed); candidates=[]; seen=set()
    # Build the full deterministic candidate sequence before evaluating it.
    for _ in range(int(n_candidates)*20):
        f=int(rng.choice([5,7,9,10,12,15])); slow=int(rng.choice([18,20,24,30,35,40,50]))
        if f>=slow: continue
        vals=(f,slow,int(rng.choice([10,12,14,16,20])),int(rng.choice([10,12,14,16,20])),int(rng.choice([2,3,4,5])),int(rng.choice([2,3,4,5])),float(rng.choice([1,1.25,1.5,1.75,2,2.5,3])),float(rng.choice([1,1.25,1.5,2,2.5,3,3.5])))
        if vals in seen: continue
        seen.add(vals); candidates.append(dict(zip(['ema_fast','ema_slow','rsi_period','stoch_period','smooth_k','smooth_d','sl_atr','rr'],vals)))
        if len(candidates)>=int(n_candidates): break
    if len(candidates)<int(n_candidates):
        raise ValueError('Could not generate enough unique parameter combinations. Reduce requested trials.')
    rows=[]; done=0
    if checkpoint_path and os.path.exists(checkpoint_path):
        try:
            with open(checkpoint_path,'r') as f: saved=json.load(f)
            if saved.get('fingerprint')==fingerprint and saved.get('config')==cfg and saved.get('candidates')==candidates:
                rows=saved.get('rows',[]); done=int(saved.get('done',0))
        except Exception:
            rows=[]; done=0
    total=len(candidates)
    if progress_callback: progress_callback(done,total,len(rows))
    for idx in range(done,total):
        p=candidates[idx]
        tr=btc_backtest(train,p,fee_bps,slippage_bps); va=btc_backtest(valid,p,fee_bps,slippage_bps)
        if len(tr)>=min_trades and len(va)>=max(30,int(min_trades*.5)):
            ts=summarize_btc(tr); z=summarize_btc(va)
            score=min(ts['Expectancy'],z['Expectancy'])-.10*z['Max DD']/max(1,len(va))
            rows.append({**p,'train_trades':len(tr),'train_expectancy':ts['Expectancy'],'train_pf':ts['Profit factor'],'validation_trades':len(va),'validation_net':z['Net PnL'],'validation_expectancy':z['Expectancy'],'validation_pf':z['Profit factor'],'validation_dd':z['Max DD'],'validation_total_r':z['Total R'],'score':score})
        done=idx+1
        # Checkpoint every 5 trials and at completion, limiting disk I/O.
        if checkpoint_path and (done%5==0 or done==total):
            tmp=checkpoint_path+'.tmp'
            with open(tmp,'w') as f: json.dump({"fingerprint":fingerprint,"config":cfg,"candidates":candidates,"done":done,"rows":rows},f)
            os.replace(tmp,checkpoint_path)
        if progress_callback: progress_callback(done,total,len(rows))
    if not rows: raise ValueError('No candidates passed trade-count filters. Increase history or lower minimum trades.')
    rank=pd.DataFrame(rows).sort_values(['score','validation_pf'],ascending=False).reset_index(drop=True)
    robust=rank[(rank.train_expectancy>0)&(rank.validation_expectancy>0)&(rank.train_pf>1)&(rank.validation_pf>1)]
    if robust.empty: raise ValueError('No robust profitable candidate found: none had positive expectancy and profit factor above 1 in BOTH training and validation. Do not force a profitable-looking setting; gather more data or redesign the entry/exit rules.')
    rank=robust.reset_index(drop=True); best=rank.iloc[0].to_dict(); p={k:best[k] for k in ['ema_fast','ema_slow','rsi_period','stoch_period','smooth_k','smooth_d','sl_atr','rr']}
    tt=btc_backtest(test,p,fee_bps,slippage_bps)
    result=(rank,p,{'train':summarize_btc(btc_backtest(train,p,fee_bps,slippage_bps)),'validation':summarize_btc(btc_backtest(valid,p,fee_bps,slippage_bps)),'final_test':summarize_btc(tt)},tt)
    if checkpoint_path and os.path.exists(checkpoint_path):
        try: os.remove(checkpoint_path)
        except OSError: pass
    return result

def latest_btc_signal(df,p):
    x=add_btc_indicators(df,p['ema_fast'],p['ema_slow'],p['rsi_period'],p['stoch_period'],p['smooth_k'],p['smooth_d'])
    if len(x)<3:return None,x
    last=x.iloc[-2]; prev=x.iloc[-3]
    side='LONG' if last.long_condition and not prev.long_condition else ('SHORT' if last.short_condition and not prev.short_condition else None)
    sig={'side':side,'time':str(last.datetime),'close':float(last.close),'ema_fast':float(last.ema_fast),'ema_slow':float(last.ema_slow),'stoch_k':float(last.stoch_k),'stoch_d':float(last.stoch_d)} if side else None
    return sig,x
