from decimal import Decimal
from sqlalchemy import String,Numeric,ForeignKey,JSON,CheckConstraint
from sqlalchemy.orm import Mapped,mapped_column
from .database import Base
class Dryer(Base):
    __tablename__='dryers'
    id:Mapped[int]=mapped_column(primary_key=True)
    code:Mapped[str]=mapped_column(String(40),unique=True)
    state:Mapped[str]=mapped_column(String(20),default='active')
class DryingRun(Base):
    __tablename__='drying_runs'
    id:Mapped[int]=mapped_column(primary_key=True)
    blend_id:Mapped[int]=mapped_column(ForeignKey('blends.id'),unique=True)
    dryer_id:Mapped[int]=mapped_column(ForeignKey('dryers.id'))
    input_moisture:Mapped[Decimal]=mapped_column(Numeric(9,4))
    after_moisture:Mapped[Decimal|None]=mapped_column(Numeric(9,4),nullable=True)
    starts_at:Mapped[str]=mapped_column(String(40))
    ends_at:Mapped[str]=mapped_column(String(40))
    state:Mapped[str]=mapped_column(String(20),default='drying')
    measurements:Mapped[list]=mapped_column(JSON)
    input_snapshot:Mapped[dict]=mapped_column(JSON)
    accepted_by:Mapped[str|None]=mapped_column(String(120),nullable=True)
class PelletPack(Base):
    __tablename__='pellet_packs'
    __table_args__=(CheckConstraint('kg > 0'),)
    id:Mapped[int]=mapped_column(primary_key=True)
    check_id:Mapped[int]=mapped_column(ForeignKey('pellet_checks.id'))
    code:Mapped[str]=mapped_column(String(80),unique=True)
    kg:Mapped[Decimal]=mapped_column(Numeric(12,3))
    state:Mapped[str]=mapped_column(String(20),default='open')
    certificate_snapshot:Mapped[dict]=mapped_column(JSON)
class BlendShipment(Base):
    __tablename__='blend_shipments'
    id:Mapped[int]=mapped_column(primary_key=True)
    code:Mapped[str]=mapped_column(String(70),unique=True)
    customer:Mapped[str]=mapped_column(String(120))
    state:Mapped[str]=mapped_column(String(20),default='loading')
    manifest:Mapped[dict|None]=mapped_column(JSON,nullable=True)
    receipt_snapshot:Mapped[dict|None]=mapped_column(JSON,nullable=True)
    dispatched_by:Mapped[str|None]=mapped_column(String(120),nullable=True)
class ShipmentPack(Base):
    __tablename__='shipment_packs'
    id:Mapped[int]=mapped_column(primary_key=True)
    shipment_id:Mapped[int]=mapped_column(ForeignKey('blend_shipments.id'))
    pack_id:Mapped[int]=mapped_column(ForeignKey('pellet_packs.id'),unique=True)
    pack_snapshot:Mapped[dict]=mapped_column(JSON)
class RepackBatch(Base):
    __tablename__='repack_batches'
    __table_args__=(CheckConstraint('loss_kg >= 0'),CheckConstraint('source_total_kg = target_total_kg + loss_kg'),)
    id:Mapped[int]=mapped_column(primary_key=True)
    check_id:Mapped[int]=mapped_column(ForeignKey('pellet_checks.id'))
    request_key:Mapped[str]=mapped_column(String(80),unique=True)
    source_total_kg:Mapped[Decimal]=mapped_column(Numeric(12,3))
    target_total_kg:Mapped[Decimal]=mapped_column(Numeric(12,3))
    loss_kg:Mapped[Decimal]=mapped_column(Numeric(12,3))
    certificate_snapshot:Mapped[dict]=mapped_column(JSON)
    note:Mapped[str]=mapped_column(String(600),default='')
    created_by:Mapped[str]=mapped_column(String(120))
    created_at:Mapped[str]=mapped_column(String(40))
class RepackSource(Base):
    __tablename__='repack_sources'
    id:Mapped[int]=mapped_column(primary_key=True)
    batch_id:Mapped[int]=mapped_column(ForeignKey('repack_batches.id'))
    pack_id:Mapped[int]=mapped_column(ForeignKey('pellet_packs.id'),unique=True)
    kg:Mapped[Decimal]=mapped_column(Numeric(12,3))
    pack_snapshot:Mapped[dict]=mapped_column(JSON)
class RepackTarget(Base):
    __tablename__='repack_targets'
    id:Mapped[int]=mapped_column(primary_key=True)
    batch_id:Mapped[int]=mapped_column(ForeignKey('repack_batches.id'))
    pack_id:Mapped[int]=mapped_column(ForeignKey('pellet_packs.id'),unique=True)
    kg:Mapped[Decimal]=mapped_column(Numeric(12,3))
