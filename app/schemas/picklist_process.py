from pydantic import BaseModel, Field


class PickListFromSalesOrderRequest(BaseModel):
    sales_order_name: str = Field(..., min_length=1, max_length=140)


class PickListFromSalesOrderCreateRequest(PickListFromSalesOrderRequest):
    allow_partial: bool = True


class PickListFromSalesOrderShortageResponse(BaseModel):
    item_code: str
    item_name: str
    requested_qty: float
    allocated_qty: float
    shortage_qty: float


class PickListFromSalesOrderPreviewResponse(BaseModel):
    sales_order_name: str
    pick_list_name: str | None = None
    existing_pick_list: bool = False
    allocated_line_count: int
    shortage_count: int
    can_create: bool
    shortages: list[PickListFromSalesOrderShortageResponse] = Field(default_factory=list)


class PickListFromSalesOrderCreateResponse(BaseModel):
    sales_order_name: str
    pick_list_name: str
    created: bool = True
    allocated_line_count: int
    shortage_count: int
    has_shortages: bool


class PickListCompleteRequest(BaseModel):
    create_delivery_note: bool = True


class PickListCompleteResponse(BaseModel):
    pick_list_name: str
    delivery_note_created: bool
    delivery_note_name: str | None = None


class ProcessJobResponse(BaseModel):
    job_id: str
    job_type: str
    status: str
    correlation_id: str | None = None
    result: dict | None = None
    error_message: str | None = None
    error_code: str | None = None
    retryable: bool | None = None
