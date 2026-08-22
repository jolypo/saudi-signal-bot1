from sqlalchemy import create_engine, Column, Integer, String, Float, DateTime, Text, Boolean
from sqlalchemy.orm import declarative_base, sessionmaker
from datetime import datetime, timezone
Base=declarative_base()
class User(Base):
    __tablename__='users'
    id=Column(Integer,primary_key=True)
    telegram_id=Column(Integer,unique=True,index=True)
    username=Column(String)
    active=Column(Boolean,default=True)
    created_at=Column(DateTime,default=lambda: datetime.now(timezone.utc))

class Trade(Base):
    __tablename__='trades'; id=Column(Integer,primary_key=True); symbol=Column(String); name=Column(String); entry=Column(Float); entry_time=Column(DateTime); tp1=Column(Float); tp2=Column(Float); tp3=Column(Float); sl=Column(Float); probability=Column(Float); score=Column(Float); strategy=Column(String); market_regime=Column(String); sector=Column(String); exit=Column(Float); exit_time=Column(DateTime); result=Column(Float); max_profit=Column(Float,default=0); max_drawdown=Column(Float,default=0); status=Column(String,default='OPEN'); notified_levels=Column(Text,default=''); paper=Column(Boolean,default=True); reasons=Column(Text,default='')

def db(url):
    e=create_engine(url); Base.metadata.create_all(e); return sessionmaker(bind=e)
