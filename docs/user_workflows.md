# Shop-BE 用户工作流程

## 用户注册

用户注册主要分为以下几个部分:

1. 填写用户名、密码（以及确认密码）和邮箱信息。
   - 如果邮箱不合规则，将返回状态码为 400 的 `INVALID_OPERATION` 错误
   - 如果用户名或邮箱已被其他用户使用，将返回状态码为 409 的 `RESOURCE_CONFILCT` 错误
2. 请求邮箱验证码，并填写验证码。
   - 如果验证码错误，将返回状态码为 400 的 `CAPTCHA_FAILED` 错误
3. 点击注册按钮，完成注册。

会依次调用以下几个接口:

1. `GET /user/captcha/register` 发送邮箱验证码
   - 如果发送成功，在返回的 `data` 字段会包含一个 `request_id` 字符串，用于后续的注册请求，客户端应妥善存储该值，该值的有效期为 5 分钟
2. `POST /user/register` 注册用户
   - 请求体应是一个包含用户名、密码、邮箱和验证码的表单
   - 请求头必须包含一个 `request-id` 字段，值为上一步返回的 `request_id` 值

## 用户登录

用户登录主要分为以下几个部分:

1. 填写用户名和密码。
   - 如果用户名或密码错误，将返回状态码为 401 的 `AUTH_FAILED` 错误
2. 点击登录按钮，完成登录。

会依次调用以下几个接口:

1. `POST /user/login` 登录用户
   - 请求体应是一个包含用户名和密码的表单
   - 如果登录成功，返回的 `data` 字段会包含一个 `token` 字符串，用于后续的鉴权请求，token 的类型为 Bearer Token。

## 找回密码

找回密码流程类似注册用户，不同点主要在于注册不允许使用已注册的用户名和邮箱，而找回密码则需要使用已注册的邮箱。

1. 填写邮箱信息。
   - 如果邮箱未注册，将返回状态码为 404 的 `NOT_FOUND` 错误
2. 请求邮箱验证码，并填写验证码。
   - 如果验证码错误，将返回状态码为 400 的 `CAPTCHA_FAILED` 错误
3. 填写完成新密码（以及确认密码）。
4. 点击确认按钮，完成密码修改。

会依次调用以下几个接口:

1. `GET /user/captcha/recover` 发送邮箱验证码

   - 如果发送成功，在返回的 `data` 字段会包含一个 `request_id` 字符串，用于后续的密码修改请求，客户端应妥善存储该值，该值的有效期为 5 分钟
2. `POST /user/recover` 修改密码
    - 请求体应是一个包含邮箱、验证码和新密码的表单
    - 请求头必须包含一个 `request-id` 字段，值为上一步返回的 `request_id` 值
    - 如果修改成功，返回的 `data` 字段会包含该邮箱对应的用户名以便于用户登录

