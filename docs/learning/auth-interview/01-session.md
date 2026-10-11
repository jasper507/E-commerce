# 01 登录后为什么能认出本人：Session与退出失效

2026-10-11。Supporting / Pair。适用源码快照：`a5d4b04995552a340a471d31e68973e89f67e54c`。本专题用于解释项目面试中的请求身份与安全边界；资料整理完成不表示拥有者已独立掌握。

[认证专题总览](../auth-current-overview-study-guide.md) · [本轮归档验证](../../verification/auth-interview-knowledge-base-publication.md) · [学习导航](../README.md)

## 本专题前后变化

- **整理前：** 登录、Session、CSRF、本人数据限制分散在源码、早期API测试和Phase9C代理入口记录里，容易把“浏览器记住用户”说成“前端传一个用户ID”。
- **本次整理：** 串起密码校验、服务端会话、浏览器Cookie、后续请求恢复身份及退出失效；区分认证失败、CSRF失败与业务数据越权。
- **整理后与边界：** 能沿一条实际请求解释为什么后端认出本人，以及退出到底删除了什么。本轮不修改认证实现，不重新登录或写数据库；已有HTTP证据保留原日期与环境。

## 先从顾客经历理解问题

Alice打开商品页时可以匿名浏览。她登录后打开购物车，再刷新订单页，后端仍应知道这是Alice；她退出后再次请求个人订单，后端应拒绝。这里需要解决两个不同问题：第一次登录怎样证明她拥有Alice账号，以及后续每次请求怎样恢复这个已验证身份。

**Authentication（认证）是确认“当前请求是谁”。Session（服务端会话）是把已确认的登录状态保存在服务器，让浏览器凭一个会话标识继续使用它。Cookie是浏览器保存并按规则随请求发送的小段数据。** 本项目采用Django原生账号与Session，API只配置DRF的`SessionAuthentication`；没有给前端签发JWT或长期登录Token，也没有让请求体的`user_id`决定身份。

会话并不解决所有权限问题。后端恢复出Alice后，还必须检查她能否查看订单A。`request.user`可靠并不意味着任意订单ID都归她；这个边界由本人过滤和业务规则守住，见本专题其他问题页。

## 必须守住的规则

1. 密码由后端验证，前端显示“已登录”不能建立服务端身份。
2. 后续请求依赖有效的服务端会话，客户端自称的用户名、用户ID或`is_staff`不能替代它。
3. 建立或清除会话的POST也必须通过CSRF检查；拒绝退出请求时，原登录状态应保留。
4. 正确退出要清除服务端当前会话，而不只是清空Vue变量或移除页面上的用户名。
5. 错误凭据不创建新的认证会话；错误响应不区分“账号不存在”和“密码错误”。如果调用前本来已有会话，失败登录也不能被理解为自动退出。

CSRF（跨站请求伪造）指别的网站利用浏览器自动携带的Cookie，诱使后端执行用户并未主动授权的写操作。它与Session一起出现的原因，后续CSRF问题页会具体展开；此页先保留它在真实流程中的检查位置。

## 从登录到个人订单的一次完整请求流

本页保留完整文字流程；登录前后CSRF的请求顺序与失败分支见[CSRF问题页](02-csrf.md)，本人数据限制见[数据权限问题页](03-ownership.md)。

### 1. 页面取得CSRF Cookie

Vue登录页面调用`login()`。前端先执行`GET /api/auth/csrf/`，后端的`get_token(request)`让响应设置CSRF Cookie，返回204。这个接口允许匿名访问，**得到CSRF Cookie不等于已经登录，也不等于创建了一个认证Session**。

前端从`document.cookie`读`csrftoken`，在之后的登录POST里加`X-CSRFToken`。登录请求使用`credentials: 'same-origin'`，同源请求按浏览器规则携带Cookie。当前页面使用相对地址`/api/auth/...`；本地Vite代理与独立Nginx部署入口负责把API送到Django，而不是前端直接连接MySQL。

### 2. Django验证请求和密码

`POST /api/auth/login/`先经过显式`csrf_protect`。CSRF不通过时就返回403，登录方法不会继续建立会话。通过后，`LoginSerializer`校验字段，并把例如`"  ALICE_01  "`规范成`"alice_01"`；密码不做去首尾空格处理，因为空格可能是密码本身。

`authenticate()`委托Django认证后端校验密码哈希并检查账号是否可用。Password Hash（密码哈希）是服务器保存的不可直接还原的密码校验结果；这里不是把数据库密码当明文拿出来与请求比较。账号不存在、密码错误或账号停用，当前接口均返回400及`Invalid username or password.`。格式不合法的字段也可能在Serializer阶段得到400，不能把所有400都称为密码错。

注册与登录是两个独立动作。注册成功返回201、使用`create_user`存储密码哈希，但注册接口不会顺带调用`django_login`。

### 3. 服务端建立会话，浏览器保存标识

认证成功后，`django_login(request, user)`在会话中写入用户ID、认证后端和用于核验登录状态的认证哈希，并把当前请求用户设为Alice。框架在响应阶段保存发生修改的会话，并通过`Set-Cookie`让浏览器保存标识；响应正文只给`id/username/is_staff`，不是登录Token。

项目没有覆盖`SESSION_ENGINE`，使用Django默认数据库Session后端，会话保存在MySQL的`django_session`表。浏览器得到的`sessionid`是会话标识，**不是完整用户资料，也不是明文密码**。后续请求的身份必须由服务端按这个标识查回和验证。

登录还会处理已有Session。对于已有匿名会话，Django轮换Session Key，让登录前的标识不直接继续承担登录后的身份；如果已有认证会话属于不同用户或认证哈希不匹配，会清掉旧会话。这样能降低Session Fixation（会话固定）的风险，即有人试图让受害者登录到攻击者提前知道的会话标识中。需要说准确：同一已登录身份再次登录可能保留Session Key，**不能泛称每次成功登录都换新Key**。登录同时轮换CSRF Token，因此登录前缓存的Header随后可能失效。

这几步由Django框架完成，不是项目重新实现了一套会话协议。项目的核心选择是正确调用它、保护入口，并按它恢复出的用户约束业务。

### 4. 下一次请求重新恢复身份

Alice随后请求`GET /api/auth/me/`或`GET /api/orders/`。浏览器自动携带符合域名、路径、SameSite等规则的Session Cookie。

`SessionMiddleware`先提供会话，`AuthenticationMiddleware`根据会话恢复用户；实际会话和用户查询可在使用时延后执行，不必把它讲成每个中间件进入时立刻各查一条SQL。DRF的`SessionAuthentication`使用这个用户，检查必要的CSRF，再由`IsAuthenticated`决定是否允许进入受保护视图。

GET是安全读取方法，不要求额外CSRF Header，但仍要求有效登录。订单视图读取的是`request.user`的订单，客户端加`?user_id=Bob`不能变成Bob。POST下单在身份和CSRF检查通过后，才进入业务校验、幂等和库存事务；安全检查与库存正确性各自负责不同规则。

当没有有效会话、会话已过期或服务器找不到对应记录，恢复结果是匿名身份。当前仅Session认证的配置下，匿名`GET /api/auth/me/`实际返回403，而不是惯常套用的401。DRF在认证类没有提供`WWW-Authenticate`挑战头时，把未认证异常映射为403；源码与项目API测试、Phase9C HTTP观察一致。

## 用具体值区分浏览器与服务器的状态

下面的`S_old/S_new`和`C_old/C_new`仅为教学代号，不是真实Cookie或Token。

| 时点 | 浏览器侧 | MySQL/请求侧 | 业务含义 |
| --- | --- | --- | --- |
| 取CSRF后 | CSRF Cookie `C_old` | 尚无认证身份 | 可以发受CSRF保护的登录请求，不代表可以看本人订单 |
| 匿名Session登录成功 | Session标识变为`S_new`，CSRF变为`C_new` | 当前Session记录Alice身份，当前请求用户为Alice | 同一浏览器的后续请求可恢复Alice |
| GET me | 携带`S_new`，无须CSRF Header | 有效会话恢复Alice | 返回200及Alice的有限资料 |
| 用旧`C_old`尝试退出 | 当前Cookie还是`C_new`，Header旧 | 请求被CSRF拒绝，Session仍有效 | 返回403；之后me仍200 |
| 用当前`C_new`正确退出 | 响应删除Session Cookie | 原Session行删除，当前请求变匿名 | 返回204；之后me403 |

Cookie并不是服务器给浏览器的一张永久通行证。它要与仍有效的服务端会话记录配合；在服务器删除记录后，即使有人保留同一个旧标识，也不能仅靠这个标识重新恢复该会话。

## 两段核心代码：登录建立身份，退出撤销当前会话

[登录源码，固定快照第46–69行](https://github.com/jasper507/E-commerce/blob/a5d4b04995552a340a471d31e68973e89f67e54c/backend/apps/accounts/views.py#L46-L69)：

```python
@method_decorator(csrf_protect, name="dispatch")
class LoginView(APIView):
    permission_classes = (AllowAny,)

    def post(self, request):
        # authenticate 验证密码哈希；django_login 建立 Session，不给前端发长期 Token。
        serializer = LoginSerializer(
            data=request.data,
        )
        serializer.is_valid(raise_exception=True)
        user = authenticate(
            request=request,
            username=serializer.validated_data["username"],
            password=serializer.validated_data["password"],
        )
        if user is None:
            return Response(
                {"detail": "Invalid username or password."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        django_login(request, user)

        return Response(SessionUserSerializer(user).data)
```

`AllowAny`只说明匿名用户也能尝试登录，不能解释为跳过CSRF或任何密码都可通过。显式装饰器先保护请求；Serializer负责字段；`authenticate`证明账号凭据；`django_login`才把认证结果变成可持续使用的会话。这四步不能合并成“前端收到200就算登录”。

[退出源码，固定快照第79–85行](https://github.com/jasper507/E-commerce/blob/a5d4b04995552a340a471d31e68973e89f67e54c/backend/apps/accounts/views.py#L79-L85)：

```python
@method_decorator(csrf_protect, name="dispatch")
class LogoutView(APIView):
    permission_classes = (AllowAny,)

    def post(self, request):
        django_logout(request)
        return Response(status=status.HTTP_204_NO_CONTENT)
```

`django_logout`会`flush`当前会话：清空数据、删除服务端Session并更换当前请求的用户状态。响应阶段，Session中间件删除浏览器里的Session Cookie。允许匿名访问让正确携带CSRF的重复退出仍能204；它不意味着匿名退出可以不带CSRF。GET退出为405，不会退出。

**什么时候失效？** 在服务端成功执行会话删除后，后续重新加载这个会话的请求就不能再通过它恢复Alice，不必等待Cookie自然到期。Cookie删除通知到达浏览器是客户端同步的一步；如果退出响应丢失，浏览器可能仍留旧Cookie，但它不应让已删除的服务端会话复活。这个响应丢失与旧标识独立重放在本项目尚未专门注入验证，不能列为本轮实测。

退出只针对当前会话，不是把Alice所有设备全部踢出；也不能撤销已经在另一个请求里恢复身份并开始执行的业务。一个在途下单请求仍可能完成，退出不等于业务事务回滚。若业务要求“全设备退出”或“退出后连在途敏感操作也不能继续”，需要额外定义会话撤销和业务检查时点。

## 为什么当前选Session，替代方案怎样比较

项目是一个同源浏览器访问的Django模块化单体，用户数和生产流量不作真实运营声明。Django已有成熟密码校验、Session、中间件、Admin和CSRF机制，Session能用最少的新协议把顾客登录与后台管理接入业务闭环。它还让当前会话退出有直接的服务端删除点。

代价是服务端保留状态，身份恢复依赖会话存储。数据库会话读取不是零成本，多实例共享同一个Session后端才能一致；如果会话存储故障，不能承诺认证仍可用。退出本身也依赖删除成功；当前没有为数据库故障设计另一个认证兜底，更不能把报错当成已安全退出。

| 选择 | 解决什么实际需求 | 代价与当前采用理由 |
| --- | --- | --- |
| 当前数据库Session＋Cookie | 同源网页与Django Admin共享成熟认证设施，当前会话能服务端撤销 | 有状态并依赖MySQL；本项目足够支撑闭环，无须先增加身份服务 |
| 服务器保存的随机Token＋Authorization Header | 移动端或独立客户端显式带凭据，减轻浏览器自动Cookie带来的部分CSRF场景 | 仍需服务器校验/撤销记录及安全存储；不是改个Header就完成设备管理、过期和密钥轮换 |
| JWT签名凭据 | 多服务可验证签名、有效期和声明，降低每次都查中心会话的需求 | 即时退出/角色变更/设备撤销通常要短有效期、刷新凭据或撤销记录等配套；本项目没有对应实现或对照收益 |

JWT（JSON Web Token）是带签名的声明载体，Cookie是浏览器发送数据的机制，二者不属于同一层。JWT也可以装进Cookie；只要浏览器自动带登录凭据，仍需考虑CSRF。把凭据放`localStorage`再主动加Header会改变攻击面，但XSS（页面脚本被恶意代码执行）可能读取凭据，不能因此声称它“比Session绝对安全”。以上替代是设计比较，未在本项目实现或做性能对照。

当前Session Cookie设置`HttpOnly=true`、`SameSite=Lax`，普通页面脚本不能通过`document.cookie`读取它，但浏览器仍能随请求携带它。HttpOnly减少直接读取Cookie的风险，不阻止所有XSS或以用户身份发请求。CSRF Cookie允许页面脚本读，供Header使用。Secure属性本地为false、非本地配置为true；本地HTTP实测不能证明公网HTTPS已经配置或安全策略由真实浏览器执行。

## 403、400、204分别代表什么

| 具体请求 | 当前实际状态 | 后续是否仍登录 |
| --- | --- | --- |
| 匿名GET me | 403，身份不满足`IsAuthenticated` | 没有登录身份 |
| 缺CSRF的POST登录 | 403，CSRF拒绝 | 不由该请求新建认证会话 |
| 格式正确但凭据错误的POST登录 | 400，通用凭据错误 | 不由该请求新建认证会话；原已有状态不是退出 |
| 合法登录后GET me | 200，本人的`id/username/is_staff` | 是 |
| 缺/旧CSRF或错误Origin的POST退出 | 403，CSRF拒绝 | 已有会话保留 |
| 有效CSRF的POST退出 | 204，无响应正文 | 当前会话清除 |
| GET退出 | 405，方法不允许 | 已有会话保留 |

不能只看403就断言“登录过期”，也不能只看403就断言“CSRF坏了”。要结合方法、错误正文、入口和之后的me请求判断。客户端也不能把本项目400凭据错误套成通用401约定。

## 已有证据支持到哪里

**历史API层证据：** [Session API测试源码](https://github.com/jasper507/E-commerce/blob/a5d4b04995552a340a471d31e68973e89f67e54c/backend/apps/accounts/tests/test_session_auth_api.py)使用`APIClient(enforce_csrf_checks=True)`，覆盖注册不自动登录、用户名规范化、匿名Session Key与CSRF轮换、密码/停用账号、匿名me403、staff使用同一套Session登录契约、错误退出保留会话、重复退出204及GET退出405。源码断言不是本轮新运行结果。[MVP-1历史验证](../../verification/mvp-1-authenticated-cart.md)记录2026-09-18全项目64 passed、12 subtests；不能把这个总数当作64项认证专项测试。

**历史真实入口证据：** [Phase9C验证](../../verification/phase-9c-session-csrf-operations.md)和[原始HTTP JSON](../../verification/phase-9c-session-csrf-http.json)记录2026-10-11独立新库、真实Nginx `http://127.0.0.1:8080`、独立CookieJar的15项HTTP检查。合法登录后me和公开导航后me均200，MySQL本次Session为1、`last_login`更新、CSRF轮换；缺Header、旧Header、错误Origin退出均403，紧接me200、本次Session仍1；合法退出204，随后me403、Session0。临时账号与本次会话精确清理，商品/订单没有写入。

这组证据比只读源码多证明了代理入口的请求传递与真实Session增删，但它使用HTTP客户端：**没有验收浏览器登录UI、真实浏览器SameSite/HttpOnly执行、用户多设备退出、Session固定攻击全过程、服务器重启后保持、会话存储故障或并发退出与交易交错。** 它也不代表订单/支付/中间件在该部署里已走完交易。本轮只复核这些历史文件、摘录和链接，不重跑数据库实验。

## 如果重新做，我会怎样调整

在仍是同源Django单体的前提下，**保留Session方案与后端本人数据过滤**。现有问题不是“没有JWT”，而是完整产品的会话生命周期还没有明确：当前未自设Session有效期，框架默认是14天、默认不在每次请求上保存续期；这来自当前配置与框架源码静态核对，不是等待14天的实测。没有空闲期限、绝对期限、多设备查看/撤销和全设备退出契约，Vue也没有接通退出操作入口。

我会先从真实产品需求确定“多久自动退出、当前设备退出还是所有设备、退出响应未知如何提示”，再接上前端正确退出与错误状态处理，最后用最小测试证明服务端旧会话不再恢复身份、失败退出保留状态。若新增密码修改或高风险操作，再设计重新认证与会话失效规则，而不是默认所有页面都增加一次密码确认。

如果增加移动端或多个独立服务，才重新评估Header Token或JWT。采用前要明确刷新、撤销、角色变化的生效时间和凭据存储，成本是更多协议、边界与验证；不能只因为面试出现JWT名词就替换现有方案。

必要安全补充也要依据使用场景安排：当前登录没有专门的失败限速/锁定策略，密码强度校验不能阻止在线猜测。真正扩大公开访问前，应设计有误伤边界的限速和审计；这与已有秒杀Lua准入不是同一项功能。本轮不实现这些候选，也不自动扩展为认证性能实验。

解释清楚“密码验证→服务端会话→Cookie标识→request.user→本人数据限制→退出删除”并能指出上述证据边界，就达到本专题的整理停止条件。独立讲述与修改能力由后续互动验收，不能从材料已经发布推断。
