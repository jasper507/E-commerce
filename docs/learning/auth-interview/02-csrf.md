# 02 已登录为什么还要CSRF：保护购买、取消与退出请求

2026-10-11，Supporting / Pair。适用源码快照：`a5d4b04995552a340a471d31e68973e89f67e54c`。[认证与权限总览](../auth-current-overview-study-guide.md) · [本轮发布与核对](../../verification/auth-interview-knowledge-base-publication.md) · [历史Session/CSRF真实HTTP证据](../../verification/phase-9c-session-csrf-operations.md)。

本专题帮助你解释一个具体面试追问：**服务器认出这是我的账号，为什么还不能马上允许购买或取消？** 最小可信依据是实际登录/退出代码、前端取得并发送令牌的方式，以及Phase9C的15项真实HTTP检查。本轮只整理这些材料，不重新操作账号、数据库或部署服务。

## 本专题前后变化

开始前，项目已经使用Session Cookie与CSRF保护，登录后的令牌轮换和代理入口拒绝行为已有证据；这些规则分散在代码、测试与部署指南中。本专题把它们串成“取得令牌 → 登录 → 轮换 → 业务写请求 → 拒绝后的确认”的完整流程。

完成后，能够区分身份、请求来源和资源归属三道门槛，也能说明为什么登录入口仍要显式防护。**没有新增安全机制，没有把HTTP客户端检查扩大成浏览器攻击测试，也没有把Token校验说成用户确实点过按钮的证明。**

## 1. 业务场景：坏网站想借用你的登录身份

你在商城登录了Alice账号，准备购买一件耳机。浏览器保存了商城的Session Cookie，后续访问商城会按Cookie规则把它带回。服务器读到这个会话，可以识别Alice。

随后你打开一个第三方页面。攻击者想诱导浏览器向商城发送“购买耳机”“取消订单”或“退出登录”的请求。如果服务器只要看见有效Session就接受写操作，这次请求便可能借用Alice身份执行Alice没有打算做的事。CSRF（Cross-Site Request Forgery，跨站请求伪造）防护处理的就是这种风险。

这里是威胁模型，不是本项目已复现的漏洞。当前Cookie设有SameSite=Lax；跨站POST是否发送Cookie、跨源JavaScript能否发送带自定义Header的请求，都受浏览器规则限制。不能把攻击流程讲成“所有第三方页面都能直接拿到商城Cookie”。我们需要理解的是：**身份凭据可能由浏览器自动附带，后端还必须判断写请求是否满足令牌与来源规则。** Django官方用恶意页面借已登录用户凭据执行操作说明这一风险，也提醒GET等安全方法不应承担危险业务变化。[Django 5.2 CSRF说明](https://docs.djangoproject.com/en/5.2/ref/csrf/)。

购买请求即使CSRF通过，还要继续校验地址属于本人、活动有效、幂等Key、库存和订单事务；取消订单即使通过，也要检查订单归属与可取消状态。CSRF不会替代这些业务规则。

## 2. 三个不同的问题：你是谁、请求是否符合防护规则、你能操作什么

| 机制 | 回答的问题 | 本项目例子 |
| --- | --- | --- |
| Session Cookie | 服务器认出哪个登录用户？ | `sessionid`对应服务器保存的登录会话，得到`request.user` |
| CSRF Cookie、Header与来源检查 | 写请求是否满足当前防护规则？ | 当前`csrftoken`配合`X-CSRFToken`，还要核对请求Origin等条件 |
| 权限与数据归属 | 这个用户能操作这笔数据吗？ | 当前用户只能购买时使用自己的地址、取消自己的订单 |

Cookie是浏览器保存并按规则发送的小份数据。项目的`sessionid`承担身份凭据，设为HttpOnly，页面JavaScript不能直接读取；`csrftoken`允许页面读取，用于构造CSRF Header。读到`csrftoken`不等于登录了，获取它也不会直接建立登录Session。

CSRF Token不是密码，不作为用户ID使用，也不是订单幂等Key。当前Django默认CSRF机制没有把每个令牌强制绑定到某条Session；它依靠令牌秘密、请求携带方式和来源规则实现防护。不要说“后端根据CSRF Token查出Alice”。Alice身份来自Session。

`HttpOnly`限制JavaScript读取Cookie；`Secure`限制Cookie在HTTPS连接发送；`SameSite`限制某些跨站场景的Cookie携带。它们与CSRF校验互相配合。当前本机HTTP实测Session的HttpOnly=true、CSRF的HttpOnly=false、两者SameSite=Lax、Secure=false；这不是生产HTTPS已验收的声明。

## 3. 完整流程：登录前拿一次，登录后用当前值

以下用A和B标记两份不同的有效令牌，不是实际可发送的字面值；S表示已登录Session。

1. 页面调用`GET /api/auth/csrf/`。后端`get_token(request)`触发设置CSRF Cookie，返回204。浏览器保存令牌A；这时仍然是游客。
2. 前端从`document.cookie`读取当前`csrftoken`，发送`POST /api/auth/login/`。请求体只有用户名和密码；Header携带`X-CSRFToken: A`。浏览器同时按规则带回CSRF Cookie A。
3. 登录视图的显式CSRF保护先检查请求。通过后才进行输入校验和密码认证。缺少Header时返回403，不应创建登录Session；密码错误时是另一条400错误路径。
4. 密码正确，调用`django_login(request, user)`建立登录会话。成功响应包含用户摘要并设置Session Cookie S，同时轮换CSRF秘密，浏览器里的CSRF Cookie变成B。
5. 后续`GET /api/auth/me/`带Session S即可确认身份，不需要CSRF Header。GET读请求的安全边界是权限和数据归属，不能偷偷在GET里实现取消订单等业务写操作。
6. 下一次创建订单、秒杀下单、确认收货或发起Mock支付前，当前前端会再次调用`ensureCsrfCookie()`，成功后读取浏览器当前值B，再发送写请求Header B。Session S仍按浏览器规则附带。
7. 普通已登录业务写请求由DRF的SessionAuthentication进行CSRF检查；通过后才进入权限判断、View和业务服务。秒杀因此在Lua准入与MySQL下单事务之前先守住身份和CSRF边界。
8. 退出调用`POST /api/auth/logout/`，当前Header B与正确来源通过后才清除Session，返回204；再调用`me`得到403。

<!-- BOARD:CSRF -->

[本地时序图](../assets/auth-interview-2026-10-11/csrf-sequence/diagram.png) · [可编辑Mermaid源](../assets/auth-interview-2026-10-11/csrf-sequence/diagram.mmd)。图和上面的流程表达同一规则；代理入口的历史实测只覆盖登录、身份保持与退出，普通业务写请求主要依据源码和既有接口测试。

项目没有把A永久保存在某个登录状态变量中。每次后续写操作取得当前Cookie，可以适应登录轮换；但两次请求之间仍可能有其他标签页重新登录，不能据此保证永远不出现旧令牌错误。当前前端也没有遇到403就自动重复购买的逻辑。

## 4. 为什么登录接口也需要保护

登录入口必须允许游客访问，因此使用`AllowAny`。这句话只表示“调用它不要求已有登录身份”，不表示“关闭CSRF”。

DRF的SessionAuthentication在没有已登录用户时不会执行同样的CSRF强制检查。若登录接口只依赖它，就不能覆盖匿名登录请求。因此项目给注册、登录、退出的`dispatch`显式套上`csrf_protect`，在进入具体方法前执行Django防护。DRF官方明确提醒：匿名请求的这条行为不适合直接作为登录接口的保护。[DRF SessionAuthentication说明](https://www.django-rest-framework.org/api-guide/authentication/#sessionauthentication)。

Login CSRF（登录跨站伪造）还有一类容易忽略的场景：攻击者企图让受害者浏览器登录到攻击者控制的账号，受害者后续可能把地址、购买意图等资料写进错误账号。因为还没有受害者的Session，也不能把“游客不涉及CSRF”当作结论。

真实核心代码一：登录视图明确保护匿名入口，再认证密码与建立会话。[固定版本源码](https://github.com/jasper507/E-commerce/blob/a5d4b04995552a340a471d31e68973e89f67e54c/backend/apps/accounts/views.py#L46)。

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

最值得理解的顺序是“CSRF保护 → 输入校验 → 密码认证 → 建立Session”。不能为了消除403把装饰器删掉，也不能先建立会话，再补检查令牌。

## 5. 前端怎样把当前令牌交给后端

真实核心代码二：取得Cookie后再读取；获取失败或缺Cookie时，不继续发出业务写请求。[固定版本源码](https://github.com/jasper507/E-commerce/blob/a5d4b04995552a340a471d31e68973e89f67e54c/frontend/src/api/auth.ts#L44)。

```typescript
export async function ensureCsrfCookie(): Promise<string> {
  // 先向后端取得 CSRF Cookie，再从浏览器 Cookie 中读取它；前端不能自行伪造 Token。
  const response = await fetch(`${AUTH_API_BASE}/csrf/`, {
    credentials: 'same-origin',
  })
  if (!response.ok) {
    throw new AuthApiError(response.status, await errorMessage(response))
  }

  const token = csrfToken()
  if (!token) throw new Error('浏览器没有收到 CSRF Token。')
  return token
}
```

前端调用登录时把返回值放进`X-CSRFToken`；订单、地址、购物车、秒杀和支付等写入口复用同一取值方式。当前实现读的是Cookie值，后端负责按Django规则验证。安全依据不是“JavaScript完全不能修改Cookie”，而是第三方站点在通常的浏览器隔离条件下不能随意读取、设置受害者商城来源的这份秘密并完成合规请求。自己的页面、自己的开发者工具能看到令牌，并不构成突破别人的账号。

`credentials: 'same-origin'`表示该fetch只在同源请求中附带凭据。项目使用相对路径`/api/...`，本地部署由同一Nginx入口提供页面和API；请求不是把Cookie当作JSON字段或手写Cookie Header发送。

这个简单方案的代价是每次写操作前多一个取得CSRF Cookie的请求。它降低长期缓存旧令牌的风险，但不提供跨标签页的绝对同步，也不替代写操作幂等。

## 6. 缺Header、旧Header、错误Origin分别怎样失败

令牌与来源是不同门槛。假设当前Cookie是B、Session是S：

| 写请求 | 发生什么 | 身份是否因此消失 |
| --- | --- | --- |
| 有S与Cookie B，但没有CSRF Header | CSRF拒绝403 | 不应因为拒绝而退出登录 |
| 有S、Cookie B、旧Header A | A不满足当前秘密，拒绝403 | Session仍然有效 |
| 有S、Cookie B、Header B，但Origin不被接受 | 来源门槛失败，拒绝403 | Session仍然有效 |
| S有效、Cookie与Header均当前、来源有效 | 通过CSRF，继续检查权限与业务规则 | 可以继续，但业务仍可能400/404/409等拒绝 |
| 只有Cookie B与Header B，没有登录Session | CSRF令牌不赋予顾客身份 | 受保护业务仍由IsAuthenticated拒绝 |

不要把所有403都显示或解释成“登录过期”。本项目SessionAuthentication下，匿名访问`me`也是403；而已登录用户的错误CSRF写请求403后，`me`仍可200。应结合接口、返回正文与身份确认来区分原因。

还要区分两种实际错误正文。注册/登录/退出的显式Django CSRF失败通过项目`csrf_failure`：API响应为`{"detail": "CSRF verification failed."}`，非API路径保留Django HTML失败页。普通已登录API写请求通过DRF SessionAuthentication的检查，失败由框架抛出PermissionDenied，`detail`以`CSRF Failed:`开头并带原因。不能把前一份固定JSON当作所有业务接口403的共同契约。

当前前端会读取JSON中的`detail`，请求失败就返回错误给页面。它没有自动补令牌并重放购买；即使以后增加这样的恢复，也必须先辨明请求是否进入过业务层，以及交易接口是否保存原Key和原参数。

## 7. 同源、SameSite与CORS，为什么不能混成一个概念

Origin（来源）由协议、主机与端口组成。项目历史检查的正常入口是`http://127.0.0.1:8080`；把Host仍保留为这个入口，却发送`Origin: http://localhost:8080`，来源便不同。端口或协议改变也会改变来源。浏览器的同源策略限制不同来源之间读取响应等能力，但一些跨源写入仍可能被允许；这正是不能只靠“攻击者读不到响应”保护写操作的原因。[MDN同源策略](https://developer.mozilla.org/en-US/docs/Web/Security/Defenses/Same-origin_policy)。

SameSite针对Cookie的“站点”关系，判断方式不同于Origin。不同子域可以同站却不同源；因此不能把SameSite=Lax解释成“已经保证每次请求同源”。它是减少风险的一层，不是替代Header和后端来源检查。

CORS（跨源资源共享）规定浏览器是否允许前端跨源访问响应以及某些请求；自定义Header等请求通常还涉及预检。它不是服务器上的“当前用户拥有订单”检查，也不能覆盖所有表单或简单请求带来的CSRF风险。当前页面与API选用同源部署，无需为了登录随意开放CORS。[DRF的AJAX、CSRF与CORS说明](https://www.django-rest-framework.org/topics/ajax-csrf-cors/)。

Phase9C中特别核对Nginx把Host中的8080端口传给Django，避免页面实际来源与后端看到的来源不一致。正确处理代理链比“见403就加一个任意可信来源”更可解释。此处只引用已经检查的本机HTTP链路，不声称HTTPS代理头和公网域名已经验收。

## 8. 为什么当前用Session，换JWT会改变哪些代价

当前是同一个Web商城的Vue页面和Django后端，Django原生Session、CSRF和Admin可以形成统一身份基础。Session保存在服务端，退出可以清除服务器会话；代价是服务端存储与请求识别成本。项目没有为技术名词改用另一套长期Token。

JWT（JSON Web Token，带签名的身份声明）可以支持某些跨服务或原生客户端场景，但“用了JWT就不会有CSRF”是错误判断。若JWT也放在自动携带的Cookie里，类似CSRF威胁仍须处理。若由JavaScript把Bearer Token放进Authorization Header，凭据不会以同样方式自动附带，传统Cookie CSRF面会变化；同时令牌保存、XSS读取、刷新、退出与吊销需要另做设计。

XSS（跨站脚本注入，攻击者脚本在商城自身来源运行）和CSRF也不同。如果攻击者已经在商城来源执行脚本，便可能读取CSRF Token并发起合规请求，HttpOnly仅阻止直接读取Session Cookie，不能阻止脚本借浏览器发送它。这个判断来自浏览器与当前令牌机制的推导，不代表项目已经做过XSS攻防或完整安全审计。

其他候选包括只依赖SameSite、只核对Origin，或改用与Session绑定的服务端令牌。它们各有兼容性、来源缺失处理和存储成本；本项目已有成熟框架组合，没有观测到需要自行实现CSRF协议的业务收益。这里是设计比较，不是候选攻击实验结果。

## 9. 哪些证据可以向面试官讲

2026-10-11的[Phase9C记录](../../verification/phase-9c-session-csrf-operations.md)使用独立CookieJar，经真实Nginx入口`http://127.0.0.1:8080`访问Django与独立MySQL新库，15项HTTP状态全部与预期一致。[原始JSON](../../verification/phase-9c-session-csrf-http.json)保留每项请求的结果，没有记录真实密码、Cookie、Token或Session Key。

| 历史实测 | 支持的结论 |
| --- | --- |
| 匿名me403、获取CSRF204、缺Header登录403，Session仍0 | 取得令牌不等于登录；缺令牌登录没有建立会话 |
| 正确同源登录200，Session为1、last_login更新、CSRF轮换 | 登录建立服务端身份，令牌登录前后发生变化 |
| 登录后me与公开导航后的me均200 | 这条真实HTTP链路保留了同一身份 |
| 缺Header、旧Header、错误Origin退出各403；每次紧接me200、Session仍1 | 拒绝写请求没有清掉已有会话；当前秘密和来源门槛有效 |
| 当前Header正确退出204，me403、Session为0 | 正确退出使该登录会话失效 |
| 精确清理后用户/Session/商品/订单均0 | 临时账号与本次会话已清理，没有建立交易数据 |

已有`test_session_auth_api.py`使用`APIClient(enforce_csrf_checks=True)`表达注册、登录、轮换、旧Token拒绝、退出与非API错误页等Oracle；普通订单、秒杀等接口也有各自的Session/CSRF测试。强制开启CSRF检查比默认绕过的测试客户端更适合验证这里的规则。本轮只阅读其代码与已有记录，没有重跑这些测试。

证据的边界也要一起说：15项包含每次错误退出后的身份确认，并非15种攻击；HTTP客户端检查了Cookie属性，不代表浏览器实际执行SameSite或HttpOnly策略；没有真实第三方站点攻击、XSS试验、浏览器UI登录或浏览器跨站行为验收，没有公网HTTPS、重启后会话保持、完整匿名Session固定攻击实验，也没有在这15项里完成下单或支付。这些材料证明特定请求链和拒绝行为，不能宣称系统已经通过全方位安全验证。

## 10. 如果重新做，我会先改哪里

我会保留“同源Web入口＋Django Session＋框架CSRF”，因为当前业务与Admin都适合这套基础。先改进对403原因的表达和恢复边界，让页面能区分身份失效、CSRF失败和业务权限拒绝；当前仅返回detail与HTTP状态，对用户下一步引导还不够明确。这是候选改进，本轮没有新增稳定错误码。

第二个候选是减少每次写操作前的额外CSRF GET：启动时或确实缺Cookie时获取，普通写操作直接读取最新Cookie；登录成功后以轮换后的值为准，并明确处理其他标签页变化。代价是恢复状态更复杂。必须先有请求次数或用户等待方面的真实痛点，才能把它说成值得做的优化；现在没有测量得出它是性能瓶颈。

我不会给所有失败交易无条件自动重试。一个安全拒绝本身没有进入业务事务，但网络失败可能发生在事务已提交、客户端没收到响应之后；认证恢复与交易恢复需要分别识别。若以后设计重试，普通建单和秒杀仍要保留原Key、原地址、原商品参数，再确认已有结果，不能把“修好Token”当作新订单授权。

若实际进入公网部署，再补HTTPS、可信代理、Cookie Secure与浏览器入口的针对性验证；若新增App或多来源客户端，再根据身份生命周期讨论Token方案。采用门槛是明确的新业务场景和最小可复现证据。本专题在现有流程、两处源码与证据边界讲清后收束，不为准备面试自动新增攻击实验或更换认证框架。
