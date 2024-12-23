import logging
from datetime import timedelta
from uuid import UUID, uuid4

import bcrypt
from email_validator import EmailNotValidError, validate_email
from fastapi import APIRouter, Depends, Form, Header, Request
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session

from Models.database import AddressDb, UserDb
from Models.response import BaseResponse, ExceptionResponseEnum, StandardResponse
from Models.user import (
    AddressRequest,
    Gender,
    Permission,
    Token,
    UpdateUser,
    User,
    UserAddress,
)
from Services.Cache.cache import cache
from Services.Database.database import get_db
from Services.Limiter.slow_limiter import freq_limiter
from Services.Mail.mail import Purpose, send_captcha
from Services.Security.user import (
    ACCESS_TOKEN_EXPIRE_MINUTES,
    create_access_token,
    get_current_user,
    verify_user,
)

user_router = APIRouter(prefix="/user")
logger = logging.getLogger("user")


@user_router.get("/captcha/register", response_model=BaseResponse[str])
@freq_limiter.limit("1/minute")
async def user_req_register_captcha(
    request: Request, email: str
) -> StandardResponse[str]:
    try:
        emailinfo = validate_email(email, check_deliverability=False)
        normalized_email = emailinfo.normalized
    except EmailNotValidError:
        raise ExceptionResponseEnum.INVALID_OPERATION()

    ip = request.client.host if request.client else "Unknown"
    captcha = send_captcha(normalized_email, Purpose.REGISTER, ip)
    request_id = uuid4().hex
    await cache.set(key=request_id, value=captcha, ttl=300)
    return StandardResponse[str](
        status_code=200, message="Captcha sent", data=request_id
    )


@user_router.get("/captcha/recover", response_model=BaseResponse[str])
@freq_limiter.limit("1/minute")
async def user_req_recover_captcha(
    request: Request, email: str
) -> StandardResponse[str]:
    try:
        emailinfo = validate_email(email, check_deliverability=False)
        normalized_email = emailinfo.normalized
    except EmailNotValidError:
        raise ExceptionResponseEnum.INVALID_OPERATION()

    ip = request.client.host if request.client else "Unknown"
    captcha = send_captcha(normalized_email, Purpose.RECOVER_PASSWORD, ip)
    request_id = uuid4().hex
    await cache.set(key=request_id, value=captcha, ttl=300)
    return StandardResponse[str](
        status_code=200, message="Captcha sent", data=request_id
    )


@user_router.post("/register", response_model=BaseResponse, status_code=201)
@freq_limiter.limit("10/minute")
async def user_reg(
    request: Request,
    email: str = Form(),
    username: str = Form(),
    password: str = Form(),
    gender: str = Form(),
    captcha: str = Form(),
    request_id: str = Header(convert_underscores=True),
    db: Session = Depends(get_db),
) -> StandardResponse[None]:
    try:
        emailinfo = validate_email(email, check_deliverability=False)
        normalized_email = emailinfo.normalized
    except EmailNotValidError:
        raise ExceptionResponseEnum.INVALID_OPERATION()
    is_init_user = db.query(UserDb).first() is None

    try:
        gender_data = Gender(int(gender))
    except ValueError:
        raise ExceptionResponseEnum.INVALID_OPERATION()

    if (
        db.query(UserDb)
        .filter(UserDb.email == normalized_email or UserDb.username == username)
        .first()
        is not None
    ):
        raise ExceptionResponseEnum.RESOURCE_CONFILCT()

    if (
        cached_captcha := await cache.get(request_id)
    ) is None or cached_captcha != captcha:
        raise ExceptionResponseEnum.CAPTCHA_FAILED()

    await cache.delete(request_id)

    db.add(
        UserDb(
            uid=request_id,
            email=normalized_email,
            username=username,
            password=bcrypt.hashpw(bytes(password, "utf-8"), bcrypt.gensalt()),
            permission=Permission.ADMIN() if is_init_user else Permission.USER(),
            birthday=None,
            gender=gender_data.value,
        )
    )
    db.commit()
    return StandardResponse[None](status_code=201, message="User created")


@user_router.post("/login", response_model=BaseResponse[Token])
@freq_limiter.limit("10/minute")
async def user_login(
    request: Request,
    body: OAuth2PasswordRequestForm = Depends(),
    db: Session = Depends(get_db),
) -> StandardResponse[Token]:
    user: UserDb | None = (
        db.query(UserDb).filter(UserDb.username == body.username).first()
    )

    if user is None or not bcrypt.checkpw(
        bytes(body.password, "utf-8"), bytes(user.password, "utf-8")
    ):
        raise ExceptionResponseEnum.AUTH_FAILED()

    token = create_access_token(
        data={"sub": user.username, "id": user.uid},
        expires_delta=timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES),
    )

    return StandardResponse[Token](data=Token(access_token=token, token_type="bearer"))


@user_router.post("/recover", response_model=BaseResponse)
@freq_limiter.limit("10/minute")
async def user_recover(
    request: Request,
    email: str = Form(),
    password: str = Form(),
    captcha: str = Form(),
    request_id: str = Header(convert_underscores=True),
    db: Session = Depends(get_db),
) -> StandardResponse[str]:
    if (record := db.query(UserDb).filter(UserDb.email == email).first()) is None:
        raise ExceptionResponseEnum.NOT_FOUND()

    if (
        cached_captcha := await cache.get(request_id)
    ) is None or cached_captcha != captcha:
        raise ExceptionResponseEnum.CAPTCHA_FAILED()

    await cache.delete(request_id)

    record.password = bcrypt.hashpw(bytes(password, "utf-8"), bcrypt.gensalt()).decode(
        "utf-8"
    )
    username = record.username
    db.commit()

    return StandardResponse[str](
        status_code=200, message="Password updated", data=username
    )


@user_router.put("/profile/{uid}", response_model=BaseResponse)
async def user_update(
    uid: UUID,
    body: UpdateUser,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> StandardResponse[None]:
    if uid.hex != user.uid:
        assert verify_user(user, Permission.ADMIN)

    if (record := db.query(UserDb).filter(UserDb.uid == uid.hex).first()) is not None:
        if body.birthday is not None:
            record.birthday = body.birthday
        if body.gender is not None:
            record.gender = body.gender.value
        
        if body.permission is not None and record.permission != body.permission.value:
            assert verify_user(user, Permission.ADMIN)
            record.permission = body.permission()
        if body.password is not None:
            record.password = bcrypt.hashpw(
                bytes(body.password, "utf-8"), bcrypt.gensalt()
            ).decode("utf-8")
        # db.query().filter().update
        db.commit()
        return StandardResponse[None](status_code=200, message="User updated")
    else:
        raise ExceptionResponseEnum.NOT_FOUND()


@user_router.get("/profile", response_model=BaseResponse[User])
async def user_profile(
    user: User = Depends(get_current_user),
) -> StandardResponse[User]:
    return StandardResponse[User](data=user)


@user_router.get("/address", response_model=BaseResponse[UserAddress])
async def get_address(
    user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> StandardResponse[list[UserAddress]]:
    record = db.query(AddressDb).filter(AddressDb.uid == user.uid).all()

    return StandardResponse[list[UserAddress]](
        status_code=200,
        message=None,
        data=[
            UserAddress(
                uid=item.uid,
                aid=item.aid,
                is_default=item.is_default,
                address=item.address,
                phone=item.phone,
                name=item.name,
            )
            for item in record
        ],
    )


@user_router.post("/address", response_model=BaseResponse, status_code=201)
async def add_address(
    body: AddressRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> StandardResponse[None]:
    if body.is_default:
        db.query(AddressDb).filter(AddressDb.uid == user.uid).update(
            {"is_default": False}
        )
    db.add(AddressDb(**body.to_address(user.uid).model_dump()))
    db.commit()
    return StandardResponse[None](status_code=201, message="Address added")


@user_router.put("/address/{aid}", response_model=BaseResponse)
async def update_address(
    aid: UUID,
    body: AddressRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> StandardResponse[None]:
    if (
        record := db.query(AddressDb).filter(
            AddressDb.uid == user.uid and AddressDb.aid == aid.hex
        )
    ) is not None:
        if body.is_default:
            db.query(AddressDb).filter(AddressDb.uid == user.uid).update(
                {"is_default": False}
            )
        record.update(body.model_dump())  # type: ignore
        db.commit()
        return StandardResponse[None](status_code=200, message="Address updated")
    raise ExceptionResponseEnum.NOT_FOUND()


@user_router.delete("/address/{aid}", response_model=BaseResponse)
async def delete_address(
    aid: UUID,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> StandardResponse[None]:
    if (
        record := db.query(AddressDb)
        .filter(AddressDb.uid == user.uid and AddressDb.aid == aid.hex)
        .first()
    ) is not None:
        db.delete(record)
        db.commit()
        return StandardResponse[None](status_code=200, message="Address deleted")
    raise ExceptionResponseEnum.NOT_FOUND()
